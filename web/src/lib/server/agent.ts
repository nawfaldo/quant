// The account page's agent: Qwen3.5 on the local llama-server (tools\start_llm.bat), with
// read-only tools over the live database, the repo and the research notes (agentTools.ts).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { webdb } from './webdb.ts';
import { callTool, REPO, TOOL_LABELS, TOOL_SPECS } from './agentTools.ts';

const LLM_URL = 'http://127.0.0.1:8080';

/** Qwen's recommended sampling for Qwen3.5 (model card), per mode — same as the bot project. */
const SAMPLING = {
	think: { temperature: 1.0, top_p: 0.95, top_k: 20, min_p: 0, presence_penalty: 1.5 },
	chat: { temperature: 0.7, top_p: 0.8, top_k: 20, min_p: 0, presence_penalty: 1.5 }
};
/** Reasoning tokens per step before the step is redone without thinking (~35s at ~43 tok/s). */
const THINK_BUDGET = 1500;
/** Tool rounds per answer; past this the model must answer from what it has. */
const MAX_STEPS = 8;
/** History sent per request (~4 chars/token), well inside the 64k context. */
const HISTORY_CHARS = 60_000;

// --- Storage: one shared list of chats, in web.db (login records which account was on screen) ---
webdb.exec(`DROP TABLE IF EXISTS agent_messages`); // the first, single-chat version (never held rows)
webdb.exec(`CREATE TABLE IF NOT EXISTS agent_chats (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	login TEXT NOT NULL,
	title TEXT NOT NULL,
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL
)`);
webdb.exec(`CREATE TABLE IF NOT EXISTS agent_chat_messages (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	chat_id INTEGER NOT NULL REFERENCES agent_chats(id) ON DELETE CASCADE,
	role TEXT NOT NULL,
	text TEXT NOT NULL,
	error INTEGER NOT NULL DEFAULT 0,
	created_at INTEGER NOT NULL
)`);

export type AgentMessage = { role: 'user' | 'assistant'; text: string; error: boolean; createdAt: number };
// When the chat was last looked at: replies after it are unread.
if (!webdb.prepare('PRAGMA table_info(agent_chats)').all().some((c) => c.name === 'read_at')) {
	webdb.exec('ALTER TABLE agent_chats ADD COLUMN read_at INTEGER NOT NULL DEFAULT 0');
	webdb.exec('UPDATE agent_chats SET read_at = updated_at');
}

/** Chats whose reply is being written right now (shown as typing in the list). */
const typingChats = new Set<number>();
export const setTyping = (chatId: number, on: boolean) => void (on ? typingChats.add(chatId) : typingChats.delete(chatId));

export type AgentChat = { id: number; title: string; preview: string; updatedAt: number; unread: number; typing: boolean };

/** Every chat (one list shared by all pages and accounts), most recent first, with the last message as the preview. */
export function agentChats(): AgentChat[] {
	return webdb
		.prepare(
			`SELECT c.id, c.title, c.updated_at,
			        (SELECT m.text FROM agent_chat_messages m WHERE m.chat_id = c.id ORDER BY m.id DESC LIMIT 1) AS preview,
			        (SELECT COUNT(*) FROM agent_chat_messages m WHERE m.chat_id = c.id AND m.role = 'assistant' AND m.created_at > c.read_at) AS unread
			 FROM agent_chats c ORDER BY c.updated_at DESC`
		)
		.all()
		.map((r) => ({
			id: Number(r.id),
			title: String(r.title),
			preview: String(r.preview ?? ''),
			updatedAt: Number(r.updated_at),
			unread: Number(r.unread ?? 0),
			typing: typingChats.has(Number(r.id))
		}));
}

export function markChatRead(chatId: number) {
	webdb.prepare('UPDATE agent_chats SET read_at = ? WHERE id = ?').run(Date.now(), chatId);
}

export function createAgentChat(login: string, firstMessage: string): number {
	const title = firstMessage.replace(/\s+/g, ' ').trim().slice(0, 40);
	const now = Date.now();
	return Number(webdb.prepare('INSERT INTO agent_chats (login, title, created_at, updated_at) VALUES (?, ?, ?, ?)').run(login, title, now, now).lastInsertRowid);
}

/** Chats are shared by every account; this only checks the chat exists. */
export function chatExists(chatId: number): boolean {
	return !!webdb.prepare('SELECT 1 FROM agent_chats WHERE id = ?').get(chatId);
}

export function deleteAgentChat(chatId: number) {
	webdb.prepare('DELETE FROM agent_chat_messages WHERE chat_id = ?').run(chatId);
	webdb.prepare('DELETE FROM agent_chats WHERE id = ?').run(chatId);
}

export function agentMessages(chatId: number): AgentMessage[] {
	return webdb
		.prepare('SELECT role, text, error, created_at FROM agent_chat_messages WHERE chat_id = ? ORDER BY id')
		.all(chatId)
		.map((r) => ({ role: r.role as AgentMessage['role'], text: String(r.text), error: !!r.error, createdAt: Number(r.created_at) }));
}

export function addAgentMessage(chatId: number, role: AgentMessage['role'], text: string, error = false) {
	const now = Date.now();
	webdb.prepare('INSERT INTO agent_chat_messages (chat_id, role, text, error, created_at) VALUES (?, ?, ?, ?, ?)').run(chatId, role, text, error ? 1 : 0, now);
	webdb.prepare('UPDATE agent_chats SET updated_at = ? WHERE id = ?').run(now, chatId);
}

// --- Prompt ---
let overview = '';
try {
	overview = readFileSync(join(REPO, 'AGENT.md'), 'utf8').slice(0, 6000);
} catch {}

function systemPrompt(login: string) {
	return [
		"You are the agent inside the user's quant trading dashboard, chatting like iMessage.",
		'Write short, plain text like a text message: no Markdown, no headings, no tables, no bold. Use line breaks for lists.',
		`Today is ${new Date().toISOString().slice(0, 10)}. The account open on screen is MT5 login ${login}.`,
		'',
		'IMPORTANT: you know NOTHING about the user\'s accounts, trades, P&L, positions, strategies, code or research',
		'except what the tools return in this conversation. For any such question, call a tool FIRST, before writing',
		'any answer. Never invent trades, numbers, strategy names or prices. Every number you write must come from a',
		'tool result. If the tools return nothing, say there is nothing (e.g. "no trades closed today").',
		'For "today", "this week" etc. pass the since date to closed_trades or pnl_breakdown.',
		'Money is in USD. Strategy ids look like fn_<symbol>_<family>_<tf>-<window>.',
		'Research notes hold what was tried and why it worked or failed: search them for "why" and "what did we find" questions.',
		'',
		'## Project overview (AGENT.md)',
		overview
	].join('\n');
}

function recent(history: AgentMessage[]) {
	let total = 0;
	let start = history.length;
	while (start > 0 && total + history[start - 1].text.length <= HISTORY_CHARS) total += history[--start].text.length;
	return history.slice(Math.min(start, history.length - 1));
}

// --- llama-server ---
export class LlmError extends Error {}

async function post(path: string, body: object, signal: AbortSignal) {
	let res: Response;
	try {
		res = await fetch(LLM_URL + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal });
	} catch (e) {
		if (signal.aborted) throw e;
		throw new LlmError(`Can't reach llama-server at ${LLM_URL}. Start it with tools\\start_llm.bat.`);
	}
	if (!res.ok || !res.body) throw new LlmError(`llama-server returned ${res.status}: ${await res.text()}`);
	return res;
}

async function* events(res: Response): AsyncGenerator<any> {
	const decoder = new TextDecoder();
	let buffer = '';
	for await (const chunk of res.body!) {
		buffer += decoder.decode(chunk, { stream: true });
		const lines = buffer.split('\n');
		buffer = lines.pop()!;
		for (const line of lines) {
			if (!line.startsWith('data: ')) continue;
			const data = line.slice(6).trim();
			if (data === '[DONE]') continue;
			let evt;
			try {
				evt = JSON.parse(data);
			} catch {
				continue;
			}
			if (evt.error) throw new LlmError(evt.error.message ?? 'llama-server returned an error');
			yield evt;
		}
	}
}

/** Quick non-thinking check (the bot's router): does this message need careful reasoning? */
async function needsThinking(history: AgentMessage[], signal: AbortSignal) {
	const res = await post(
		'/v1/chat/completions',
		{
			messages: [
				{
					role: 'system',
					content:
						'Decide whether answering the user message well needs careful step-by-step reasoning: ' +
						'math, comparisons, analysis of trades or strategies, why-questions, code, or anything easy to get wrong. ' +
						'Small talk, greetings and simple lookups do not. Reply with exactly one word: THINK or CHAT.'
				},
				{ role: 'user', content: history.at(-1)!.text }
			],
			max_tokens: 3,
			temperature: 0,
			chat_template_kwargs: { enable_thinking: false }
		},
		signal
	);
	return !/chat/i.test((await res.json()).choices?.[0]?.message?.content ?? '');
}

type Msg = { role: string; content: string; tool_calls?: any[]; tool_call_id?: string; reasoning_content?: string };
export type AgentEvent = { type: 'thinking' } | { type: 'tool'; label: string } | { type: 'text'; text: string } | { type: 'done'; reply: string } | { type: 'error'; message: string };

/** One model step, streamed. Returns the text, tool calls, and whether thinking ran over budget. */
async function* step(messages: Msg[], think: boolean, tools: boolean, signal: AbortSignal) {
	const over = new AbortController();
	let text = '';
	let reasoning = '';
	let reasoningTokens = 0;
	const calls: { id: string; name: string; args: string }[] = [];
	try {
		const res = await post(
			'/v1/chat/completions',
			{
				messages,
				stream: true,
				max_tokens: THINK_BUDGET + 4096,
				...(tools ? { tools: TOOL_SPECS } : {}),
				chat_template_kwargs: { enable_thinking: think },
				...(think ? SAMPLING.think : SAMPLING.chat)
			},
			AbortSignal.any([signal, over.signal])
		);
		for await (const evt of events(res)) {
			const d = evt.choices?.[0]?.delta;
			if (!d) continue;
			if (d.reasoning_content) {
				if (reasoningTokens++ === 0) yield { type: 'thinking' } as const;
				reasoning += d.reasoning_content;
				if (reasoningTokens >= THINK_BUDGET && !text && !calls.length) {
					over.abort();
					break;
				}
			}
			for (const tc of d.tool_calls ?? []) {
				const c = (calls[tc.index ?? 0] ??= { id: '', name: '', args: '' });
				if (tc.id) c.id = tc.id;
				if (tc.function?.name) c.name += tc.function.name;
				if (tc.function?.arguments) c.args += tc.function.arguments;
			}
			if (d.content) {
				text += d.content;
				// Stream answer text only once it's clear this step is not a tool call.
				if (!calls.length) yield { type: 'text', text: d.content } as const;
			}
		}
	} catch (e) {
		if (!over.signal.aborted || signal.aborted) throw e;
	}
	return { text, reasoning, calls: calls.filter((c) => c.name), overBudget: over.signal.aborted };
}

/** A short title for a new chat, summing up what the first message wants (like Claude's chat titles). */
export async function chatTitle(firstMessage: string, signal: AbortSignal): Promise<string | null> {
	try {
		const res = await post(
			'/v1/chat/completions',
			{
				messages: [
					{
						role: 'system',
						content:
							'Write a title for a chat that starts with the user message below. 2 to 5 words, Title Case, ' +
							'naming what the user wants (e.g. "Best Trade Today", "XAUUSD Weekly P&L", "Greeting"). ' +
							'No quotes, no punctuation at the end. Reply with the title only.'
					},
					{ role: 'user', content: firstMessage.slice(0, 2000) }
				],
				max_tokens: 20,
				chat_template_kwargs: { enable_thinking: false },
				...SAMPLING.chat,
				temperature: 0.3
			},
			signal
		);
		const text: string = (await res.json()).choices?.[0]?.message?.content ?? '';
		const title = text.split('\n')[0].replace(/^["'*\s]+|["'*.\s]+$/g, '').slice(0, 40);
		return title || null;
	} catch {
		return null;
	}
}

export function setChatTitle(chatId: number, title: string) {
	webdb.prepare('UPDATE agent_chats SET title = ? WHERE id = ?').run(title, chatId);
}

export async function* agentChat(login: string, history: AgentMessage[], signal: AbortSignal): AsyncGenerator<AgentEvent> {
	const messages: Msg[] = [
		{ role: 'system', content: systemPrompt(login) },
		...recent(history).filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text }))
	];
	let reply = '';
	try {
		let think = await needsThinking(history, signal).catch((e) => {
			if (signal.aborted) throw e;
			return true;
		});
		for (let i = 0; i <= MAX_STEPS; i++) {
			const tools = i < MAX_STEPS;
			const gen = step(messages, think, tools, signal);
			let r;
			while (!(r = await gen.next()).done) {
				if (r.value.type === 'text') reply += r.value.text;
				yield r.value;
			}
			const { text, reasoning, calls, overBudget } = r.value;
			if (overBudget) {
				// Thought in circles: redo this step answering straight away.
				think = false;
				i--;
				continue;
			}
			if (!calls.length) break;
			messages.push({
				role: 'assistant',
				content: text,
				...(reasoning ? { reasoning_content: reasoning } : {}),
				tool_calls: calls.map((c, k) => ({ id: c.id || `call_${i}_${k}`, type: 'function', function: { name: c.name, arguments: c.args || '{}' } }))
			});
			for (const [k, c] of calls.entries()) {
				yield { type: 'tool', label: TOOL_LABELS[c.name] ?? 'Working…' };
				messages.push({ role: 'tool', tool_call_id: c.id || `call_${i}_${k}`, content: await callTool(c.name, c.args, { login }) });
			}
		}
	} catch (e) {
		if (signal.aborted) return;
		yield { type: 'error', message: e instanceof LlmError ? e.message : String(e) };
		return;
	}
	if (!signal.aborted) yield { type: 'done', reply: reply.trim() };
}
