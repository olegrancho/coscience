# Artifact → live chat redirect

## The problem

Opening a chat on an artifact checks it out: `create_chat` acquires the artifact
lock under holder `("chat", "chat:<tid>")` (`service.py:670`). From that moment the
artifact page is a dead end — "Open chat" (`ArtifactDetail.tsx:203`) is disabled
with a `busy — chat:ab12` tooltip and nothing links to the chat holding it. Going
back loses the chat; finding it again means hunting the program's chat list.

The split between "the artifact page" and "the chat that edits it" is the real
mistake — they should be one page. That merge is separate, larger work. This is the
stopgap.

## The change

While an artifact is chat-locked (`lock.holder_kind === "chat"`,
`lock.holder_id === "chat:<tid>"` — the window from "Open chat" until **Release**),
every route to it lands on the chat.

1. **`ArtifactDetail`** — once the query resolves, a chat-held lock renders
   `<Navigate to={/programs/:id/chat?c=<tid>} replace />` instead of the page. Every
   artifact link goes through this route, so this one guard covers the program card,
   the sprint link, bookmarks and the back button. `SprintDetail` is untouched.
   `replace` keeps the artifact page out of history so back doesn't bounce.
2. **`ArtifactDetail.tsx:143`** — `openChat`'s post-create `navigate` gains
   `replace: true`, so the first back press after creating a chat isn't swallowed.
3. **`ProgramDetail.tsx:347`** — the card links straight to the chat when `a.lock`
   is chat-held (`ArtifactRow` already carries `lock`). Same destination, without a
   flash of the artifact page.
4. **`ChatView.tsx:314,340`** — drop `full view →` and `open artifact` on bound
   chats; they'd bounce back. Going by `bound` rather than re-fetching the lock, so
   they stay gone after Release too — acceptable given the pages are merging.

A small shared `liveChatId(lock)` in `components/ui.tsx` (next to `isImageName`)
returns the tid, or `""`. No backend change — `lock` is already in both payloads.

## Accepted

Version history, revert, comments, download and discard are unreachable for a
checked-out artifact until **Release**. The page merge fixes that properly.

## Testing

One test in `ArtifactDetail.test.tsx`: a chat-held artifact redirects to the chat, a
sprint-held one doesn't.

## Out of scope

Merging the two pages. Anything about released chats — a freed artifact behaves
exactly as today, and "Open chat" still creates a new one.
