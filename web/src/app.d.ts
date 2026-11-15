// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			user: import('#lib/server/auth.ts').User;
		}
		// interface PageData {}
		interface PageState {
			account?: boolean; // the Account view is open on top of the chart page
			user?: boolean; // the User view is open on top of the chart page
			accountPath?: string; // which account page it shows (/account or /account?login=&tab=)
		}
		// interface Platform {}
	}
}

export {};
