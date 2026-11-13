import defaultAvatar from '#lib/icons/avatar.webp';

// Who is logged in: name and profile picture URL (null = the default Arisu picture).
// Shared so a change on /user shows at once next to the chart page's door.
export const me = $state<{ name: string; avatar: string | null }>({ name: '', avatar: null });
export { defaultAvatar };
