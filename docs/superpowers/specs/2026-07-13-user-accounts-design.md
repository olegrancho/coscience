# User Accounts & Attribution — Design

**Status:** Design approved (pending spec review)
**Date:** 2026-07-13
**Scope:** Near-term lightweight identity + attribution. Keycloak/OIDC is a later phase, designed for but not built here.

## 1. Summary

Give the platform real (if lightweight) user identity so people log in as
themselves and every human action is attributed. Today there is no login and
attribution is absent (votes carry only an opaque per-browser id; comments and
chat messages have no author; governance actions record no actor).

Near-term auth is **username-only, no password** — access is already gated by
ssh-tunnel/LAN, so a picked username is trusted for attribution. All identity
resolution is isolated behind one seam so a later swap to Keycloak/OIDC touches
one module and leaves attribution code untouched.

**Decisions locked in during design:**
- Username-only auth now (no password). Real auth arrives with Keycloak later.
- Curated registry (`.coscience/users.yaml`), not self-registration.
- Login cookie + `current_user()` dependency as the single identity seam.
- Attribute **all** human write actions, shown inline (no separate audit page —
  git history is the full trail).
- Flat authority: every user is a full-power overseer. No roles/permissions yet.

## 2. The identity seam (Keycloak-ready)

One new module, `coscience/auth.py`, owns *everything* about identity:
- loading + looking up the user registry,
- signing / verifying the session cookie,
- a single FastAPI dependency `current_user(request) -> User`.

Every attributed action derives its actor from `current_user` — **never** from
client-supplied data. This is the load-bearing rule: the browser cannot claim to
be someone else by sending a different `by`.

**When Keycloak lands:** only `auth.py` changes — the cookie login is replaced by
the OIDC authorization-code flow, and `current_user` reads the OIDC session/JWT
and maps its `sub` to a `User`. Attribution, endpoints, and the frontend's use of
`by` are unchanged. `username` is the stable id chosen now precisely so it can map
to a Keycloak subject later.

## 3. User registry

`.coscience/users.yaml` — curated, git-tracked, seeded:

```yaml
users:
  - username: stroganov
    name: Oleg Stroganov
    initials: OS
  - username: apathak
    name: Aish Pathak
    initials: AP
```

- `username` — stable id, used as the attribution key and future Keycloak subject.
- `name` — display name.
- `initials` — optional; for the avatar chip. If omitted, derived from `name`
  (first letter of first + last word, uppercased).

`User` model: `{username, name, initials}`. Loader + lookup live in `auth.py`.
Adding a person is a one-line edit + commit (no restart needed if the registry is
read live per request, matching the resource-pool live-read pattern).

## 4. Login & session

- `POST /api/login {username}` — if the username is in the registry, set a
  **signed** cookie and return the `User`; otherwise 401.
  - Cookie value = `username` + HMAC signature. Secret from `COSCIENCE_SECRET`
    env; if unset, generate once and persist to `.coscience/secret` (gitignored,
    mode 600). Signing prevents casual cookie-editing to impersonate — it is not
    real authentication (there is no password), just honest attribution until
    Keycloak.
  - Cookie flags: `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Secure` (served over
    a plain-HTTP ssh tunnel; revisit under TLS/Keycloak).
- `POST /api/logout` — clear the cookie.
- `GET /api/me` — the current `User`, or 401 if unauthenticated.
- `GET /api/users` — the registry list (for the login picker and display lookups).

**Gate:** `current_user` is required on all API endpoints **except** `login`,
`users`, `health`, `version`, and the static UI. Reads require login too — one
simple gate, and the login is frictionless (pick a name). Unauthenticated API
calls return 401; the frontend shows the login screen.

**Empty-registry bypass (rollout + safety):** if `.coscience/users.yaml` is
absent or lists no users, auth is **disabled** — the gate passes anonymously and
attributed actions record `by=""` (rendered "—"). This preserves today's
behavior until a deployment is seeded, avoids bricking the app if the registry is
missing, and keeps the existing (auth-less) test suite valid. Auth turns on the
moment the registry has at least one user.

## 5. Attribution (server-derived)

A `by: <username>` field is stamped from `current_user` on every human write:

| Object | Change |
|---|---|
| chat message | `{role, text, at}` → add `by` |
| sprint comment | `{id, text, added_at}` → add `by` |
| idea comment | `{id, text, added_at}` → add `by` |
| vote | replace the opaque browser id with the real `username` (one vote/user) |
| governance | approve / reject / run / send_back / demote → append to a per-sprint `decisions: [{by, action, at}]` |

- **Governance display is inline**, not a separate audit view: the sprint renders
  its `decisions` trail in context (e.g. "approved by Oleg Stroganov · 2d ago").
  This is a lightweight per-sprint list, deliberately not the design doc's
  first-class `decision_log` with machine-readable rationale — YAGNI for now; git
  history remains the complete audit trail.
- **Service methods** that perform these actions gain a `by: str` parameter,
  passed from the HTTP layer's `current_user`. Existing tests that call them
  directly pass an explicit `by`.
- **Back-compat:** records written before this change have no `by`; they render as
  "—" (unknown). Loaders default `by=""`.

## 6. Frontend

- **Login gate:** on load, call `/api/me`; on 401 show a login screen — a picker
  populated from `/api/users` (name + initials avatar) → `POST /api/login` → enter.
- **User menu** in the top bar: current user (name + initials) and a logout action.
- **Transport:** the cookie is automatic (same-origin). `api.ts` gains a global
  401 handler → drop to the login screen. The generated per-browser voter id is
  removed; votes now use the session user.
- **Show `by`:** chat bubbles, comments, votes, and sprint decision lines display
  the author as an initials avatar + name. A small shared `<UserChip username>`
  component resolves display via the `/api/users` map.

## 7. Testing

- Registry: loads seeded users; `initials` derived when omitted; unknown lookup.
- Cookie: sign→verify round-trips; a tampered/forged cookie is rejected.
- Endpoints: `login` (valid → cookie + user; unknown → 401), `logout`, `me`.
- `current_user`: rejects absent, malformed, bad-signature, and not-in-registry
  cookies; accepts a valid one.
- Attribution is server-derived: a request that sends a bogus `by` in the body is
  ignored — the recorded actor is the session user.
- Gate: an unauthenticated write returns 401; `login`/`users`/`health` are open.
- Back-compat: a pre-existing comment/vote/message with no `by` renders "—".

## 8. Non-goals (deferred)

- Passwords / real authentication (arrives with Keycloak).
- Roles & permissions / per-action authorization (everyone is a full-power
  overseer for now).
- Keycloak/OIDC implementation itself — only the seam is built.
- A dedicated audit-log view with structured rationale (git history suffices).
