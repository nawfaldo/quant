import { error, json } from '@sveltejs/kit';
import {
	addAgentMessage,
	agentChat,
	agentChats,
	agentMessages,
	chatExists,
	createAgentChat,
	deleteAgentChat,
	chatTitle,
	markChatRead,
	setChatTitle,
	setTyping
} from '#lib/server/agent.ts';
import type { RequestHandler } from './$types';

// No chat -> every chat (newest first; shared by all pages and accounts). ?chat= -> that chat's messages.
export const GET: RequestHandler = ({ url }) => {
	const chat = url.searchParams.get('chat');
	if (chat == null) return json(agentChats());
	if (!chatExists(Number(chat))) error(404, 'chat not found');
	markChatRead(Number(chat));
	return json(agentMessages(Number(chat)));
};

// The open chat got a reply while on screen: it is read.
export const PATCH: RequestHandler = ({ url }) => {
	const chat = Number(url.searchParams.get('chat'));
	if (!chatExists(chat)) error(404, 'chat not found');
	markChatRead(chat);
	return new Response(null, { status: 204 });
};

export const DELETE: RequestHandler = ({ url }) => {
	const chat = Number(url.searchParams.get('chat'));
	if (!chatExists(chat)) error(404, 'chat not found');
	deleteAgentChat(chat);
	return new Response(null, { status: 204 });
};

// Send a message (chat: null starts a new chat). The reply streams back as ndjson:
// start {chat}, thinking, tool, text..., done | error.
export const POST: RequestHandler = async ({ request }) => {
	const body = await request.json().catch(() => null);
	const login = typeof body?.login === 'string' ? body.login : '';
	const message = typeof body?.message === 'string' ? body.message.trim() : '';
	if (!message) error(400, 'message is required'); // login only tells the agent which account is on screen
	let chat = typeof body?.chat === 'number' ? body.chat : null;
	if (chat != null && !chatExists(chat)) error(404, 'chat not found');
	const isNew = chat == null;
	chat ??= createAgentChat(login, message);
	const chatId = chat;

	addAgentMessage(chatId, 'user', message);
	const history = agentMessages(chatId);
	const encoder = new TextEncoder();
	const abort = new AbortController();

	const stream = new ReadableStream({
		async start(controller) {
			const send = (data: unknown) => controller.enqueue(encoder.encode(JSON.stringify(data) + '\n'));
			send({ type: 'start', chat: chatId });
			setTyping(chatId, true);
			// A new chat gets a short title from Qwen, alongside the reply.
			const titling = isNew
				? chatTitle(message, abort.signal).then((title) => {
						if (!title || abort.signal.aborted) return;
						setChatTitle(chatId, title);
						try {
							send({ type: 'title', title });
						} catch {}
					})
				: null;
			let partial = '';
			let saved = false;
			try {
				for await (const evt of agentChat(login, history, abort.signal)) {
					if (evt.type === 'text') partial += evt.text;
					if (evt.type === 'done') {
						addAgentMessage(chatId, 'assistant', evt.reply);
						saved = true;
					} else if (evt.type === 'error') {
						addAgentMessage(chatId, 'assistant', evt.message, true);
						saved = true;
					}
					send(evt);
				}
			} catch (e) {
				if (!abort.signal.aborted) {
					addAgentMessage(chatId, 'assistant', String(e), true);
					saved = true;
					send({ type: 'error', message: String(e) });
				}
			} finally {
				// Keep whatever streamed if the page closed mid-reply.
				if (!saved && partial.trim()) addAgentMessage(chatId, 'assistant', partial.trim());
				setTyping(chatId, false);
				await titling;
				try {
					controller.close();
				} catch {}
			}
		},
		cancel() {
			abort.abort();
		}
	});
	return new Response(stream, { headers: { 'content-type': 'application/x-ndjson', 'cache-control': 'no-cache' } });
};
