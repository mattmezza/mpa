# Web artifacts — publish a page with write_file

When an answer is richer than a chat bubble can show — a report, a dashboard, a
chart, a comparison table, an interactive checklist, a slide deck, "give me a
mini-site / document for X" — publish it as a **web artifact**: a file you write
into the workspace that is served as a shareable link.

There is no dedicated artifact tool. An artifact is just files under
`artifacts/<slug>/` in the workspace, written with the **workspace file tools**
(`write_file`, `edit_file`, `list_dir`). The server exposes that directory at
`/artifacts/<slug>/` automatically.

This needs the workspace harness to be enabled (the `write_file` tool). If you
don't have `write_file`, you can't publish artifacts — tell the user to enable
the workspace in Settings → Tools.

## Publish a single page

Write a complete HTML document to `artifacts/<slug>/index.html`:

```
write_file(path="artifacts/q3-report/index.html", content="<!doctype html><html>…</html>")
```

The page is then live at `<base>/artifacts/q3-report/`, where `<base>` is this
assistant's public web address (the admin app; if `MPA_BASE_URL` is configured,
that's the base — otherwise it's the host the user reaches the admin UI on).
Give the user that link.

- `<slug>` must be letters, digits, `-` and `_` only (e.g. `q3-report`,
  `expenses_2026`). No spaces, slashes, or dots. Reuse a slug to overwrite an
  existing artifact in place; pick a fresh one for a new artifact.
- The directory is created for you on first write.

## Publish a multi-file site

Write each file under the same slug directory; link them with **relative** URLs:

```
write_file(path="artifacts/dash/index.html", content="<link href='style.css'>…<script src='app.js'></script>")
write_file(path="artifacts/dash/style.css", content="body{font-family:system-ui}")
write_file(path="artifacts/dash/app.js", content="console.log('hi')")
```

`index.html` is served at the slug root. Reference siblings as `href="style.css"`,
`src="img/logo.png"`, etc. — they resolve under `/artifacts/dash/`.

## The complexity ladder

For HTML, climb only as high as the request needs:

| Need | What to reach for |
|---|---|
| Quick report | Plain semantic HTML, no styling |
| Clean readable doc | A classless CSS framework (MVP.css / Water.css) via CDN |
| Branded / designed page | Custom CSS in `<style>`, or TailwindCSS v4 via its browser CDN build (`<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>`) |
| Interactivity | Inline JS, or Alpine.js via CDN |

## Binary / generated files (PDF, image, slides)

Produce the file on disk inside the workspace (e.g. write a PDF with `pandoc`
via `run_command_in_dir`, output into `artifacts/<slug>/`), then it's served
directly. Point `index.html` at it, or link to the file by name.

## Housekeeping

- **No TTL / expiry.** Artifacts live until you remove them. Overwrite a slug to
  update it; use `list_dir("artifacts")` to see what's published; remove an old
  one by clearing its directory with `run_command_in_dir` (`rm -rf artifacts/<slug>`)
  if that tool is available.
- **Served without authentication, by design.** The slug is public and
  guessable — anyone with the link can open it. **Don't put secrets in an
  artifact.** Pages run sandboxed (a `Content-Security-Policy: sandbox`), so
  their JavaScript can't read the admin session.
- Writing a file asks the owner for approval (the standard `write_file` prompt),
  so publishing an artifact is a confirmed action like any other workspace write.
