# Setting up databases and sign-in without breaking the official repo

The rule: **the repo ships code and a documented switch; the owner supplies the
secret.** Nothing in a pull request should need an account that only one person
has, and nothing should stop the app from starting when a switch is off.

## Principles

1. **Optional, with a working default.** Every external service is behind an env
   var. Blank means "use the local fallback", never "crash".
   Example: `TURSO_DATABASE_URL` blank or `file:./data/events.db` uses local
   SQLite; a real `libsql://` URL uses Turso.
2. **Secrets live in env vars only.** Add the *name* to `.env.example` with a
   comment, never the value. Real values go in the host's settings (Vercel /
   Render dashboard) and each developer's untracked `.env`.
3. **One concern per pull request.** Storage, sign-in and UI changes land as
   separate PRs so each can be reviewed and reverted on its own.
4. **Additive changes.** New routers and modules; existing files get one-line
   hooks (`include_router`, a settings field) so merges stay conflict-free.
5. **A test that needs no network.** Mock the provider; CI must pass without
   anyone's keys.

## Database (event log)

| Environment | Setting | Who does it |
|---|---|---|
| Local / grader | leave `TURSO_DATABASE_URL` blank | nobody |
| Shared demo | `turso db create`, then `turso db tokens create`; set `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` in the host | repo owner |

Create the database under the **team's** account, not a personal one, so it
survives a contributor leaving. Use a separate database per environment
(preview vs production). The schema is created by the app on first start.

## Sign-in (OAuth)

Not built yet; this is the approach that merges cleanly.

1. **Register the app** (GitHub: Settings > Developer settings > OAuth Apps)
   under the team's organisation. Callback URL per environment. This needs a
   human with org rights, so it is the owner's step, not a PR's.
2. **Keep the client secret on the server.** Frontend redirects to the provider;
   a backend route exchanges the code. Env vars: `GITHUB_CLIENT_ID`,
   `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`.
3. **Sessions in an httpOnly, Secure, SameSite=Lax cookie**, never localStorage.
4. **Least scope.** `read:user` for identity; add `repo` only when a feature
   needs private repos, and say why in the PR.
5. **Feature-flagged.** With the client id unset, the app runs signed-out exactly
   as today (history stays in the browser).
6. **Then** move run history to the database keyed by user id. That is a
   separate PR after sign-in works.

A no-secret alternative for a pure frontend is GitHub's device flow, but it
still needs a registered client id and does not give you server sessions.

## Checklist before merging any integration

- [ ] Works with the env var unset
- [ ] `.env.example` lists the names, no values
- [ ] No token in logs, URLs or localStorage
- [ ] Tests pass offline
- [ ] README says who creates the account and where the secret goes
