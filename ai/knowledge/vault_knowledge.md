# Secure Vault — verified application facts

This file is the retrieval corpus for the voice assistant's `VAULT_HELP`
answers. Every statement here was checked against the codebase. Anything not
written here is treated as undocumented, and the assistant says so instead of
guessing.

## What Secure Vault is

Secure Vault is a Flask web application for one signed-in owner's personal
documents and notes. It is not a multi-tenant cloud drive and it has no public
file listing. Everything is scoped to the account that signed in.

## Getting started — how to use the app

Register an account, then sign in with either your email or your username. The
dashboard is the home screen: it lists your files by folder, has the **Add
files** control, the Notepad, the voice panel, and the buttons for Settings,
Manage shares and Trash.

A normal first session looks like this: upload a file with **Add files** and
pick or name a folder; write a note in the Notepad if you want a private
encrypted note; save a recipient in Settings if you plan to share something;
create a share link from the file and copy it while it is displayed, because it
is only shown once; and use the search and folder controls on the dashboard to
find things later. Deleting a file moves it to Trash, where it stays for 30
days and can be restored from the interface.

You can drive most of this by voice: tap the microphone and speak, for example
"list my files", "open my passport", "search for invoice", "list my notes",
"how much space am I using", or "share my passport with Rahul". The voice panel
is microphone-only, records nothing, and shows the transcript of what it heard.

## Files and folders

Files are uploaded from the dashboard with the **Add files** control. A single
upload request is limited to 50 MB. Each file belongs to exactly one folder.

Built-in folders are Identity, Finance, Health, Legal. Users can create their
own folders by naming one from the Add files tile. Folders are inferred from the
documents they contain, so a folder with no files does not appear.

Supported in-browser preview is limited to PDF, PNG, JPEG, GIF and WebP. Other
types are download-only, because the server decides the MIME type and refuses to
render anything that could execute.

## Encryption

Notes are encrypted with Fernet (AES-128-CBC with an HMAC) before they are
written to the database, using the `ENCRYPTION_KEY` environment variable. If no
key is set, one is derived from `SECRET_KEY`.

Document files are stored in the uploads folder and are **not** encrypted at
rest today. The application's security model relies on account authentication,
owner-scoped queries, and file-system permissions for those files. This is a
known gap, and the assistant must not claim documents are encrypted on disk.

Pending voice actions are stored encrypted, so a database read alone does not
reveal a queued share or note.

## Notes

Notes are created in the Notepad or by voice. A note has a title and an
encrypted body. The assistant lists note titles only and never reads, speaks, or
logs note contents. Notes can be downloaded as a text file and deleted in
batches from the interface.

## Sharing

A share link points at one document or a frozen snapshot of a folder. Creating a
link requires choosing an expiry (10 minutes, 1 hour, or 24 hours) and a usage
rule (one download or multiple). Only a SHA-256 hash of the token is stored, so
the link itself is shown once and cannot be recovered later.

Links can be revoked immediately from **Manage shares**, and can be protected
with a password that is stored as a hash. Every open, password failure and
download is recorded in the link's access history with a masked IP address.

Voice sharing selects a **saved contact** by name. A dictated email address is
refused, even inside another command. Voice-created links never include a
password and never send email; the link is displayed in the voice panel so it
can be copied.

## Trash

Deleting a document moves it to Trash instead of destroying it. Trashed files
are stored compressed and keep their original size and SHA-256 checksum, so a
restore is verified byte for byte. Trash is retained for 30 days, then purged.
The assistant can open the Trash view but cannot delete, purge, restore, or move
anything by voice.

## Contacts

Contacts are saved recipients (name plus email) created in Settings. They exist
so a share can be addressed by name rather than by a spoken address. Editing or
deleting a contact later does not change a link that was already sent, because
the recipient details are snapshotted onto the link.

## Accounts and authentication

Accounts register with a username, email and password. Passwords are hashed with
Werkzeug; plaintext is never stored. Login accepts either email or username and
is rate limited per IP and identifier with an exponential lockout. Password
reset uses a signed token that expires after one hour.

Changing a password and deleting an account both require the current password;
account deletion also requires typing a confirmation phrase. Neither is
available by voice.

## Security controls in place

- CSRF protection on every POST, including JSON fetches, via Flask-WTF tokens.
- Owner-scoped queries on every document, note, contact, and share route;
  another user's identifier returns 404 rather than 403.
- Share tokens are 256-bit random values and only their hash is stored.
- Download counting for share links is atomic, so a one-time link cannot be
  raced.
- Security events (logins, share creation, voice actions) are written to an
  audit table with IP and user agent. Voice audit rows never contain a
  transcript or note text.
- Voice responses are `Cache-Control: no-store`.
- The voice panel is microphone-only and sends recognised text to the server;
  audio never leaves the browser.

## Voice assistant capabilities

Read-only requests run immediately: listing and searching files, opening a file
preview, opening a folder, describing a file (folder, size, date added), listing
folders, notes, contacts and active share links, a vault summary, and navigating
to the dashboard, shares, settings or trash.

Creating a note and preparing a share create a pending action. The user must say
"confirm" or press **Confirm**; the server then revalidates ownership, the live
file, the saved contact and the allowed options before executing. A confirmation
nonce is single-use, expires, and is bound to the signed-in owner.

## What the voice assistant cannot do

It cannot delete, purge, empty trash, move, restore, revoke a share link, upload
a file, send email, change a password, delete an account, reveal a password or
key, read note contents, accept a dictated email address, run SQL, run shell
commands, or execute generated code. It has no internet access, so it cannot
answer live questions about weather, news, prices, or sports results.

## Limits worth stating plainly

There is no cloud backup and no versioning. Login lockout state is held in
memory, so it resets when the process restarts or when multiple workers are
used. Sessions are not invalidated when a password changes. Email verification
at registration and an Emergency Lockdown mode are designed but not implemented.
