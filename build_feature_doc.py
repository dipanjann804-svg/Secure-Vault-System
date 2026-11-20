"""Generate Secure-Vault-System-Feature-Updates.docx from the project docs."""

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = "Secure-Vault-System-Feature-Updates.docx"

ACCENT = RGBColor(0x4F, 0x46, 0xE5)      # indigo
DARK = RGBColor(0x1E, 0x29, 0x3B)
MUTED = RGBColor(0x64, 0x74, 0x8B)
HDR_FILL = "4F46E5"
ALT_FILL = "EEF2FF"


# ---------------------------------------------------------------- helpers
def shade(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def no_split(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def set_cell_text(cell, text, bold=False, size=8.5, color=None, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    if color is not None:
        run.font.color.rgb = color


def add_table(doc, headers, rows, widths, body_size=8.5, zebra=True):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    hdr = table.rows[0]
    repeat_header(hdr)
    no_split(hdr)
    for i, h in enumerate(headers):
        set_cell_text(hdr.cells[i], h, bold=True, size=body_size + 0.5,
                      color=RGBColor(0xFF, 0xFF, 0xFF))
        shade(hdr.cells[i], HDR_FILL)

    for r_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for c_idx, val in enumerate(row):
            set_cell_text(cells[c_idx], str(val), size=body_size)
            if zebra and r_idx % 2 == 1:
                shade(cells[c_idx], ALT_FILL)

    for row in table.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Inches(w)
    return table


def para(doc, text="", size=10.5, bold=False, italic=False, color=None,
         space_after=6, space_before=0, align=None, style=None):
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    if align is not None:
        p.alignment = align
    if text:
        run = p.add_run(text)
        run.bold = bold
        run.italic = italic
        run.font.size = Pt(size)
        if color is not None:
            run.font.color.rgb = color
    return p


def lead_para(doc, lead, rest, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    r1 = p.add_run(lead)
    r1.bold = True
    r1.font.size = Pt(size)
    r1.font.color.rgb = DARK
    r2 = p.add_run(rest)
    r2.font.size = Pt(size)
    return p


def bullet(doc, text, size=10, bold_lead=None):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
        r.font.size = Pt(size)
    r2 = p.add_run(text)
    r2.font.size = Pt(size)
    return p


def note_box(doc, label, text, fill="FEF3C7"):
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    cell.width = Inches(6.5)
    shade(cell, fill)
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(label + " ")
    r.bold = True
    r.font.size = Pt(9)
    r2 = p.add_run(text)
    r2.font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def h1(doc, text, page_break=False):
    if page_break:
        doc.add_page_break()
    h = doc.add_heading(text, level=1)
    for r in h.runs:
        r.font.color.rgb = ACCENT
        r.font.size = Pt(17)
        r.font.name = "Calibri"
    h.paragraph_format.space_before = Pt(14)
    h.paragraph_format.space_after = Pt(6)
    return h


def h2(doc, text):
    h = doc.add_heading(text, level=2)
    for r in h.runs:
        r.font.color.rgb = DARK
        r.font.size = Pt(12.5)
        r.font.name = "Calibri"
    h.paragraph_format.space_before = Pt(10)
    h.paragraph_format.space_after = Pt(3)
    return h


def h3(doc, text):
    h = doc.add_heading(text, level=3)
    for r in h.runs:
        r.font.color.rgb = ACCENT
        r.font.size = Pt(10.5)
        r.font.name = "Calibri"
    h.paragraph_format.space_before = Pt(8)
    h.paragraph_format.space_after = Pt(2)
    return h


def add_footer(section, text):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text + "   |   Page ")
    r.font.size = Pt(8)
    r.font.color.rgb = MUTED
    run = p.add_run()
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld2 = OxmlElement("w:fldChar")
    fld2.set(qn("w:fldCharType"), "end")
    run._r.append(fld)
    run._r.append(instr)
    run._r.append(fld2)


# ---------------------------------------------------------------- document
doc = Document()

# base styles
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.paragraph_format.line_spacing = 1.12

section = doc.sections[0]
section.top_margin = Inches(0.9)
section.bottom_margin = Inches(0.9)
section.left_margin = Inches(0.9)
section.right_margin = Inches(0.9)
add_footer(section, "Secure Vault System — Feature Updates & Roadmap")

doc.core_properties.title = "Secure Vault System — Feature Updates & Roadmap"
doc.core_properties.subject = "New and planned capabilities for the Secure Vault System"
doc.core_properties.author = "Secure Vault System"
doc.core_properties.category = "Technical documentation"

# ---------------- cover
sp = doc.add_paragraph()
sp.paragraph_format.space_after = Pt(40)

t = doc.add_paragraph()
t.alignment = WD_ALIGN_PARAGRAPH.LEFT
r = t.add_run("SECURE VAULT SYSTEM")
r.bold = True
r.font.size = Pt(11)
r.font.color.rgb = ACCENT
r.font.name = "Calibri"

t2 = doc.add_paragraph()
t2.paragraph_format.space_after = Pt(4)
r = t2.add_run("Feature Updates\n& Roadmap")
r.bold = True
r.font.size = Pt(30)
r.font.color.rgb = DARK
r.font.name = "Calibri"

sub = doc.add_paragraph()
sub.paragraph_format.space_after = Pt(18)
r = sub.add_run("New capabilities designed for the vault: protected document sharing, "
                "retention and recovery, coercion resistance, and offline portability.")
r.font.size = Pt(11.5)
r.font.color.rgb = MUTED

meta = add_table(
    doc,
    ["Item", "Detail"],
    [
        ["Document", "Feature Updates & Roadmap"],
        ["Version", "1.0"],
        ["Date", "20 November 2026"],
        ["Project", "Secure Vault System (Flask 3 / SQLAlchemy / Fernet)"],
        ["Code status", "Unchanged — this release is documentation and specification only"],
        ["Companion files", "README.md (build spec) · security.md (hardening guide)"],
    ],
    widths=[1.7, 4.8],
    body_size=9,
)

doc.add_paragraph()
note_box(
    doc,
    "Scope note.",
    "Every feature in this document is specified but NOT yet implemented in app.py, models.py "
    "or the templates. Nothing here describes current behaviour. Each section states what the "
    "feature would do, how it would be built, and — honestly — how much security it actually "
    "buys. Building on top of the existing gaps listed in Section 7 would not be safe.",
    fill="FEF3C7",
)

# ---------------- 1. summary
h1(doc, "1. Executive summary", page_break=True)

para(doc, "This cycle adds no application code. It converts two rounds of feature requests into a "
          "build-ready specification, splits what exists from what does not, and scores every "
          "proposed feature so the next development slice is an explicit choice rather than a guess.")

para(doc, "Three documents changed or were produced:", space_after=4)
bullet(doc, " rewritten end-to-end: a status legend, the six core vault features with routes, "
            "data-model changes and implementation detail, an advanced feature catalogue, a "
            "27-row implementation matrix, and an honest gap list.", bold_lead="README.md")
bullet(doc, " a new Appendix A covering coercion, insider and leak threats, plus a "
            "feature-honesty table that grades each control.", bold_lead="security.md")
bullet(doc, " this document — the same material in reviewable form.", bold_lead="Feature Updates & Roadmap")

h2(doc, "1.1 What changed at a glance")
add_table(
    doc,
    ["Area", "Update"],
    [
        ["Core vault features", "6 new features specified: preview, 30-day trash, expiring share "
                                "links, account deletion, email verification, Emergency Lockdown"],
        ["Advanced features", "8 defensive/cryptographic features catalogued and honestly rated"],
        ["Workstation features", "6 usability enhancements scoped (viewer, voice memos, credentials, "
                                 "folders, sessions, PWA)"],
        ["Architecture", "Implementation matrix with defense vector, security rating, complexity "
                         "and delivery phase for all 27 items"],
        ["Security documentation", "Appendix A: insider, leak, coercion and shoulder-surfing threats"],
        ["Code", "No changes. No routes, models, migrations or templates were modified."],
    ],
    widths=[1.7, 4.8],
    body_size=9,
)

# ---------------- 2. status legend
h1(doc, "2. How to read this document")

add_table(
    doc,
    ["Label", "Meaning"],
    [
        ["Implemented", "Present in the code today and safe to rely on."],
        ["Planned", "Specified here, not yet in app.py / models.py / templates."],
        ["Prerequisite (Phase 0)", "A defect that must be fixed before the feature above it is meaningful."],
        ["Honest rating", "The real security value of a control, not its ambition. Speculative or "
                          "cosmetic features are labelled as such."],
    ],
    widths=[1.9, 4.6],
    body_size=9,
)

para(doc, "Ratings use High / Medium / Low / Speculative / Cosmetic. A high rating means the control "
          "withstands an adversary who knows how it works; a low rating means it stops only a casual "
          "observer and must not be sold as more.", space_before=6)

# ---------------- 3. implemented
h1(doc, "3. What the vault does today (Implemented)")

para(doc, "Recorded so the gap between existing and planned behaviour is unambiguous.")
add_table(
    doc,
    ["Capability", "Detail"],
    [
        ["Authentication", "Register, log in by email or username, log out; Werkzeug password hashing"],
        ["Password recovery", "Signed 1-hour reset links via Resend / Brevo HTTP APIs or Gmail SMTP"],
        ["Password change", "Requires the current password"],
        ["Document vault", "Multi-file upload (50 MB cap), folders, search, download, single and batch delete"],
        ["Encrypted notes", "Fernet-encrypted content, decrypted on read, .txt export"],
        ["Profile", "Name, phone, date of birth, gender, location, avatar upload"],
        ["Security logging", "Login attempts with IP, user agent and SUCCESS/FAILED status"],
        ["Login lockout", "Per IP + identifier, exponential backoff from 30 s"],
        ["Theme", "Dark / light mode persisted in localStorage"],
    ],
    widths=[1.7, 4.8],
    body_size=9,
)

# ---------------- 4. core features
h1(doc, "4. New core vault features (Planned)", page_break=True)

para(doc, "Each item below follows the same shape: what it does, how it would be built, and the "
          "security property it actually provides.")

h2(doc, "4.1 In-browser preview of PDFs and images")
bullet(doc, "GET /documents/<id>/preview renders inline — no download to disk just to read a file.",
       bold_lead="What it does: ")
bullet(doc, "owner-scoped query (a foreign id returns 404, never 403), server-decided MIME type, "
            "and the response carries nosniff plus Content-Security-Policy: default-src 'none'; sandbox "
            "so a crafted PDF cannot execute in the vault origin.",
       bold_lead="How: ")
bullet(doc, "only application/pdf and image/png|jpeg|gif|webp preview; everything else keeps its "
            "attachment download.", bold_lead="Allowlist: ")
bullet(doc, "Medium — reduces exposure to downloaded malicious files; the browser's own PDF "
            "sandbox does the real work.", bold_lead="Honest rating: ")

h2(doc, "4.2 Trash with 30-day retention")
bullet(doc, "Deleting sets Document.deleted_at instead of destroying the row and the file. "
            "Restore is possible for TRASH_RETENTION_DAYS (default 30).",
       bold_lead="What it does: ")
bullet(doc, "every live query adds deleted_at IS NULL (dashboard, folder counts, search, download, "
            "preview); a purge job removes expired rows on start-up and on a schedule.",
       bold_lead="How: ")
bullet(doc, "download and share endpoints refuse trashed rows, so a link to a deleted document "
            "dies immediately.", bold_lead="Interaction: ")
bullet(doc, "Medium — availability and ransomware-recovery value, not confidentiality.",
       bold_lead="Honest rating: ")

h2(doc, "4.3 Expiring, protected document sharing")
para(doc, "The motivating case: a financial document must reach somebody without being downloaded and "
          "attached to a chat app. The vault issues a protected access link instead.")

lead_para(doc, "Owner-configurable per link: ",
          "expiration (10 minutes / 1 hour / 24 hours), optional password, one-time download, "
          "maximum download count, immediate revocation, and full access history.")

h3(doc, "Token design")
bullet(doc, "secrets.token_urlsafe(32) — 256 bits from the secrets module, never random.")
bullet(doc, "only sha256(token) is stored, in an indexed unique column. The raw token is displayed "
            "once at creation and is unrecoverable afterwards; a lost link is re-issued, not re-read.")
bullet(doc, "a database leak therefore yields no usable links.")

h3(doc, "Why one-time download needs an atomic update")
para(doc, "A read-then-write counter is racy: two concurrent requests both read count = 0, both pass, "
          "and a \"single use\" link is used twice. Consumption must be a conditional UPDATE:", space_after=4)
p = doc.add_paragraph()
p.paragraph_format.left_indent = Inches(0.3)
p.paragraph_format.space_after = Pt(8)
r = p.add_run("UPDATE share_link SET download_count = download_count + 1\n"
              " WHERE id = :id AND revoked_at IS NULL\n"
              "   AND (max_downloads IS NULL OR download_count < max_downloads)")
r.font.name = "Consolas"
r.font.size = Pt(9)
r.font.color.rgb = DARK

bullet(doc, "High — bounded, revocable distribution with attribution. What it cannot do is "
            "retrieve a file a recipient has already saved.", bold_lead="Honest rating: ")

h3(doc, "Advanced: key wrapping for a named recipient")
bullet(doc, "each document gets its own random data encryption key (DEK); bytes are AES-GCM "
            "encrypted under it, and the DEK is stored wrapped.")
bullet(doc, "for a link, the DEK is wrapped for the intended recipient — X25519 SealedBox, or a key "
            "derived from the link password via scrypt/HKDF — and stored in ShareLink.wrapped_dek.")
bullet(doc, "the server unwraps only for the duration of a request, or hands back ciphertext the "
            "recipient unwraps client-side, so a server compromise alone does not expose plaintext.")
bullet(doc, "trade-off to state plainly: a forgotten link password makes that delivery unrecoverable. "
            "For a financial document that is a feature, not a bug.")

h2(doc, "4.4 Permanent account deletion")
bullet(doc, "POST /account/delete requires the current password and a typed confirmation phrase, "
            "and re-asks for the password even though the session is already authenticated.",
       bold_lead="Guard rails: ")
bullet(doc, "revoke all share links → delete documents and unlink files from uploads/ → delete notes "
            "→ delete the avatar → delete the user row. The ORM cascade covers rows; files on disk "
            "need explicit unlinking.", bold_lead="Order: ")
bullet(doc, "SecurityLog rows are either removed with the account or retained with user_id set to "
            "NULL and the login id masked. The choice is recorded once; this specification assumes "
            "deletion unless a retention policy says otherwise.", bold_lead="Logs: ")

h2(doc, "4.5 Email verification at registration")
bullet(doc, "User.email_verified defaults to false; registration issues a hashed, single-use, "
            "24-hour token and mails /verify-email/<token>.")
bullet(doc, "until verified, upload, share and download are blocked while login still works so the "
            "user can resend. Resend is rate limited to 3 per hour.")
bullet(doc, "accounts that predate the feature are migrated to verified so nobody is locked out.")
bullet(doc, "Medium — limits account takeover and throwaway registrations.", bold_lead="Honest rating: ")

h2(doc, "4.6 Emergency Lockdown")
para(doc, "One click must perform six actions, with the database work committed as one transaction and "
          "the notification sent only after commit so a mail failure cannot roll back the lock.",
     space_after=6)
add_table(
    doc,
    ["#", "Action", "Mechanism"],
    [
        ["1", "Revoke all active sessions", "Bump User.session_version so every existing cookie and "
                                            "get_id() value stops authenticating; session.clear() locally"],
        ["2", "Disable existing sharing links", "Set revoked_at on every non-revoked ShareLink owned "
                                                "by the user"],
        ["3", "Temporarily block downloads", "Set downloads_blocked_until; download, preview and share "
                                             "routes answer 423 Locked and still log the attempt"],
        ["4", "Require fresh authentication", "reauth_at marker in session; sensitive routes demand a "
                                              "password re-entry that is at most a few minutes old"],
        ["5", "Notify the account owner", "Email through the existing provider chain: what locked, when, "
                                          "from which IP and user agent, how to unlock"],
        ["6", "Preserve security logs", "Append a LOCKDOWN event; never delete SecurityLog rows — the "
                                        "trail is the point"],
    ],
    widths=[0.3, 1.9, 4.3],
    body_size=8.5,
)
para(doc, "Unlock requires the account password, clears the block and re-issues share links rather than "
          "reviving revoked ones. Lockdown is never reversible by a GET, by a token in a URL, or by the "
          "same session that triggered it without a fresh password.", space_before=6)

# ---------------- 5. advanced
h1(doc, "5. Advanced defensive & cryptographic features (Planned / Experimental)", page_break=True)

para(doc, "These are catalogued deliberately rather than accumulated. Several are valuable, a few are "
          "speculative, and some are closer to theatre than security. Shipping a security feature that "
          "does not work is worse than shipping none, so each entry carries an honest verdict.")

h2(doc, "5.1 Steganographic \"Cloaked Vault\"")
bullet(doc, "embeds encrypted notes and documents inside ordinary photos or audio (LSB steganography) "
            "so the container looks innocent.")
bullet(doc, "the carrier must be lossless — PNG, BMP or WAV. JPEG/MP3 re-encoding destroys LSB payloads, "
            "so a \"save as JPEG\" round-trip silently eats the vault.")
bullet(doc, "capacity is about 1 bit per colour-channel byte: a 1 MB payload needs a ~8 MB PNG. Payload "
            "layout is magic header + version + AES-GCM nonce + ciphertext + tag, verified before "
            "anything is written to disk.")
bullet(doc, "Low / speculative. LSB payloads are detectable by chi-square and RS analysis, and "
            "deniability is defeated by the server's own database rows, uploads listing and access "
            "logs. This hides a file in a photo; it does not defeat an adversary.",
       bold_lead="Verdict: ")

h2(doc, "5.2 Canary \"Honey-Files\"")
bullet(doc, "realistic decoy documents with a silent beacon that reports the opener's IP and device "
            "fingerprint.")
bullet(doc, "128-bit non-enumerable id; GET /canary/<id>.gif returns a 1×1 GIF with 200 and no-store, "
            "identical for hit and miss, rate limited per IP.")
bullet(doc, "works only for documents served through the vault. A file downloaded once and opened "
            "offline never fires the beacon.")
bullet(doc, "Medium — detection only. False positives from mail scanners fetching remote images are "
            "common, and fingerprinting carries privacy and legal obligations; keep it opt-in.",
       bold_lead="Verdict: ")

h2(doc, "5.3 Shamir's Secret Sharing")
bullet(doc, "fractures the master recovery key into a (k, n) threshold, removing the single point of "
            "key failure.")
bullet(doc, "GF(2⁸) split seeded from secrets; shards encoded as a wordlist so they can be printed "
            "without transcription errors; each shard carries version, index and a hash commitment so "
            "mixing generations fails loudly.")
bullet(doc, "reconstruction happens client-side or in a one-off CLI, never inside a long-running web "
            "route — k shards in process memory is the moment the scheme is defeated. The server holds "
            "at most one shard, ideally zero.")
bullet(doc, "High, conditional on operational discipline. The maths is sound; the failure mode is "
            "human — shards at the same provider, or three holders in one office.",
       bold_lead="Verdict: ")

h2(doc, "5.4 Air-gapped portable HTML vault")
bullet(doc, "one-click export of a single .html file holding the ciphertext and a Web Crypto AES engine, "
            "openable offline from a cold-storage USB stick with zero dependencies.")
bullet(doc, "PBKDF2-SHA-256 at 600k+ iterations or Argon2id via WASM; AES-GCM with a fresh nonce per "
            "export; the passphrase is typed on the air-gapped machine and never stored.")
bullet(doc, "base64 inflates payloads by ~33%, so exports are capped or chunked; some APIs are "
            "restricted on file:// and must be verified on the target machine.")
bullet(doc, "the export carries a SHA-256 of its own payload shown at download time, because a tampered "
            "HTML vault would happily exfiltrate the passphrase.")
bullet(doc, "High for cold storage — bounded by passphrase strength and by trusting the file you carried.",
       bold_lead="Verdict: ")

h2(doc, "5.5 Cryptographic time-capsules (VDF)")
bullet(doc, "repeated squaring in an RSA group of unknown order with a Wesolowski or Pietrzak proof, so "
            "the delay is verifiable without redoing the work.")
bullet(doc, "a true time-lock puzzle makes the key the VDF output, but only with a trusted modulus "
            "ceremony where nobody retains φ(N); a compromised ceremony opens the capsule instantly.")
note_box(
    doc,
    "Correction.",
    "The original request described these as \"immune to administrative or physical coercion\". That is "
    "not achievable and the claim has been removed. A VDF imposes sequential work, not a prohibition: "
    "anyone holding the ciphertext and wrapped key simply decrypts, and parallel hardware shortens the "
    "wall clock. It also does nothing against coercion, because the ciphertext is right there. Document "
    "it as \"delayed decryption\".",
)
bullet(doc, "Low / speculative — a speed bump, not a guarantee.", bold_lead="Verdict: ")

h2(doc, "5.6 Dynamic forensic watermarking")
bullet(doc, "translucent diagonal provenance stamps (CONFIDENTIAL · user@email · IP · Date) over previews "
            "and downloads, so a leaked copy points back to a recipient.")
bullet(doc, "raster burn-in for images and PDF pages (Pillow / pypdf overlay / Ghostscript), generated "
            "per request so each recipient's copy differs; HTML overlay for previews only, which is "
            "cosmetic and vanishes on copy-paste.")
bullet(doc, "position, angle and opacity vary per recipient so one crop does not remove every marker; a "
            "watermark failure must never silently fall back to an unmarked download.")
bullet(doc, "Medium — attributes leaks when the attacker is lazy. Value is deterrence and audit trail, "
            "not robustness.", bold_lead="Verdict: ")

h2(doc, "5.7 Shoulder-surfer privacy guard")
bullet(doc, "CSS window blur on tab unfocus, a 60-second inactivity mask, and hover-to-peek redaction "
            "(filter: blur(6px) removed on :hover / :focus-visible).")
bullet(doc, "optional webcam glance detection, which needs camera permission, is unreliable in poor "
            "light, and is invasive in an app whose purpose is privacy.")
bullet(doc, "Low, and explicitly not security. Blur stops a passer-by, not a screenshot, screen "
            "recorder or phone camera. Ship blur and the inactivity lock (free); ship webcam detection "
            "off by default, opt-in, visibly indicated, or omit it.", bold_lead="Verdict: ")

h2(doc, "5.8 Cryptographic \"Emergency Shredder\"")
bullet(doc, "triggered on repeated failed PINs or a duress code, permanently rendering data unreadable.")
bullet(doc, "the reliable mechanism is destroying the per-document DEK, not overwriting file bytes. Once "
            "the wrapped key is gone the ciphertext is noise — instantly, on any medium.")
bullet(doc, "overwrite remaining key material (salt, wrapped DEKs, cached plaintext) with random bytes "
            "and fsync, delete the rows, then unlink the files.")
bullet(doc, "multi-pass overwriting of user files is unreliable on SSDs (wear levelling), on "
            "copy-on-write filesystems (btrfs/ZFS), under snapshots, and on any cloud- or OneDrive-synced "
            "path — which is exactly where this project lives. Use full-disk encryption plus key "
            "destruction; never promise \"military-grade overwrite\".")
bullet(doc, "a duress code must open a decoy vault that looks completely normal, or the coercer learns "
            "they were fooled. A duress path that visibly shreds is a duress path that escalates danger.")
bullet(doc, "Medium via key destruction, Low via overwrite. The property you can defend is \"the key is "
            "gone\"; the rest is folklore.", bold_lead="Verdict: ")

# ---------------- 6. workstation
h1(doc, "6. Workstation & usability enhancements (Planned)", page_break=True)

add_table(
    doc,
    ["#", "Feature", "What it adds", "Honest caveat"],
    [
        ["6.1", "In-browser PDF viewer + signature pad",
         "No unencrypted file lands on disk just to be read or signed; the signature is captured on "
         "<canvas> and stored as an encrypted PNG",
         "A drawn signature is not a legally recognised e-signature without identity binding and an "
         "audit trail"],
        ["6.2", "Encrypted voice memo recorder",
         "MediaRecorder → WebM/Opus, encrypted in the browser before upload, so the server stores only "
         "ciphertext",
         "The server cannot transcode or index audio; key management moves to the client"],
        ["6.3", "Credentials manager with breach audit",
         "Encrypted entries per site, generator, clipboard auto-clear; HaveIBeenPwned k-anonymity "
         "range API sends only the first 5 hex characters of the hash",
         "Never send a full hash or a password; a breach hit is a prompt to rotate, not proof of "
         "compromise"],
        ["6.4", "Hierarchical folders + colour tags",
         "parent_id with a materialized path, recursive counts, tags in their own table so a document "
         "can carry several",
         "Replaces the current \"folders inferred from document rows\" hack, where an empty folder "
         "vanishes; needs its own migration"],
        ["6.5", "Multi-device session manager + kill switch",
         "A Session table (IP, user agent, created, last seen, revoked) with per-device revocation",
         "Kill switch must delete the row AND bump session_version — the second half is what "
         "invalidates a cookie already in the wild"],
        ["6.6", "Progressive Web App (PWA)",
         "Installable offline shell on desktop and mobile, with a launcher icon",
         "The service worker caches static assets only — never vault pages or decrypted content, or "
         "\"offline mode\" becomes a plaintext cache on disk"],
    ],
    widths=[0.4, 1.5, 2.5, 2.1],
    body_size=8,
)

# ---------------- 7. matrix
h1(doc, "7. Architecture & implementation matrix", page_break=True)

para(doc, "All 27 items in build order. Security rating is the honest value of the control, not its "
          "ambition. Phase 0 items are defects that must be fixed before anything above them is "
          "meaningful.")

matrix = [
    ("CSRF protection on all POSTs", "Request forgery", "Critical", "Low", "0"),
    ("Owner-scoped download/preview (retire /uploads/<filename>)", "IDOR / horizontal privilege escalation", "Critical", "Low", "0"),
    ("Encrypt documents at rest (per-doc DEK, wrapped)", "At-rest disclosure, disk seizure", "High", "Medium", "0"),
    ("Upload allowlist + magic-byte check", "Malware, polyglot files", "High", "Low", "0"),
    ("Security headers, CSP, secure cookies", "XSS, clickjacking, MitM", "High", "Low", "0"),
    ("In-browser PDF/image preview", "Download hygiene, malware exposure", "Medium", "Low", "1"),
    ("Trash with 30-day retention", "Accidental loss, ransomware recovery", "Medium", "Low", "1"),
    ("Email verification at registration", "Account takeover, spam accounts", "Medium", "Low", "1"),
    ("Permanent account deletion", "Data minimisation, GDPR erasure", "Medium", "Low", "1"),
    ("Expiring / password / one-time share links", "Uncontrolled distribution, oversharing", "High", "Medium", "2"),
    ("Share access history + revocation", "Leak attribution, incident response", "Medium", "Low", "2"),
    ("Multi-device session manager + kill switch", "Session hijacking, stolen laptop", "High", "Medium", "2"),
    ("Emergency Lockdown (6 actions)", "Active compromise", "High", "Medium", "2"),
    ("Credentials manager + HIBP audit", "Password reuse, credential stuffing", "High", "Medium", "2"),
    ("Forensic watermarking (per-recipient)", "Leak attribution, deterrence", "Medium", "Medium", "2"),
    ("Hierarchical folders + colour tags", "None direct (usability, prevents misfiling)", "N/A", "Medium", "2"),
    ("Encrypted voice memos", "At-rest audio disclosure", "Medium", "Medium", "3"),
    ("Signature pad", "Repudiation (weak)", "Low", "Low", "3"),
    ("Shamir sharding of recovery key", "Single point of key failure", "High (conditional)", "Medium", "3"),
    ("Air-gapped portable HTML vault", "Cold storage, offline access", "High (passphrase-bound)", "Medium", "3"),
    ("PWA offline shell", "Availability, UX", "Low", "Low", "3"),
    ("Canary honey-files", "Intrusion detection", "Medium (detection only)", "Medium", "4"),
    ("Steganographic cloaked vault", "Plausible deniability", "Low (speculative)", "High", "4"),
    ("Cryptographic time-capsules (VDF)", "Delayed decryption", "Low (speculative)", "Very high", "4"),
    ("Shoulder-surfer blur / inactivity mask", "Shoulder surfing", "Low", "Low", "4"),
    ("Webcam glance detection", "Shoulder surfing", "Low, high privacy cost", "High", "4 (opt-in)"),
    ("Emergency shredder", "Coercion, physical seizure", "Medium (key destruction)", "Medium", "4 (guarded)"),
]

add_table(
    doc,
    ["Feature", "Defense vector", "Security rating", "Complexity", "Phase"],
    matrix,
    widths=[2.25, 1.9, 1.05, 0.8, 0.5],
    body_size=7.8,
)

para(doc, "Phasing rules. Phase 0 is a prerequisite for everything — a share link built on top of an "
          "unscoped /uploads/<filename> route leaks files by design. Phase 2 features depend on Phase 0's "
          "at-rest encryption, because an expiring link to an unencrypted file protects the link, not the "
          "data. Phase 4 items are individually gated behind a written threat model and a documented "
          "rollback: nothing in Phase 4 may delete data irreversibly without a tested backup path.",
     space_before=8)

h2(doc, "7.1 Delivery phases")
add_table(
    doc,
    ["Phase", "Focus", "Contents", "Exit criterion"],
    [
        ["0", "Foundations",
         "CSRF on every POST, owner-scoped file routes, at-rest document encryption, upload validation, "
         "security headers, durable lockout",
         "No route serves or deletes another user's data; a stolen database dump yields no plaintext"],
        ["1", "Core vault",
         "Preview, 30-day trash, email verification, account deletion",
         "A document can be read, recovered and erased without leaving the vault"],
        ["2", "Sharing & sessions",
         "Share links, access history, session manager, Emergency Lockdown, credentials manager, "
         "watermarking, folders",
         "Distribution is bounded and revocable; a compromise can be stopped in one action"],
        ["3", "Portability & crypto",
         "Voice memos, signature pad, Shamir sharding, air-gapped HTML vault, PWA",
         "Data is recoverable and portable without the server being online"],
        ["4", "Experimental",
         "Canary files, steganography, time-capsules, privacy guard, shredder",
         "Each item has a threat model, a rollback path and an honest UI description"],
    ],
    widths=[0.5, 1.25, 2.85, 1.9],
    body_size=8,
)

# ---------------- 8. gaps
h1(doc, "8. Security invariants and known gaps", page_break=True)

h2(doc, "8.1 Invariants the new features must preserve")
bullet(doc, "Share tokens are 256-bit secrets values; only their SHA-256 hash is persisted.")
bullet(doc, "Share passwords are hashed, never stored or logged in plaintext.")
bullet(doc, "Download counting is atomic, so one-time and max-count links hold under concurrency.")
bullet(doc, "Every share, preview and trash route keeps the user_id == current_user.id filter and "
            "returns 404 for other users' resources.")
bullet(doc, "Trashed documents are excluded from download, preview and sharing.")
bullet(doc, "Emergency Lockdown never deletes SecurityLog rows.")
bullet(doc, "Account deletion unlinks files on disk as well as rows in the database.")

h2(doc, "8.2 Known gaps, ordered by risk")
add_table(
    doc,
    ["#", "Gap", "Impact"],
    [
        ["1", "No CSRF protection on POST routes (Flask-WTF is not installed)",
         "Cross-site request forgery against upload, delete, profile and password change"],
        ["2", "GET /uploads/<filename> serves any file to any logged-in user",
         "IDOR — one account can read another's documents by guessing a name"],
        ["3", "Uploaded documents are stored unencrypted",
         "Only notes use Fernet; a disk or database leak exposes every file"],
        ["4", "Upload MIME type comes from the browser, with no extension allowlist or magic-byte check",
         "Malware and polyglot uploads; the type also drives how files are served"],
        ["5", "Login lockout tracker is in-memory",
         "Resets across gunicorn workers and restarts (Procfile runs --workers 2)"],
        ["6", "Reset tokens are reusable for their full hour and sessions survive a password change",
         "A leaked reset link or stolen cookie outlives the credential rotation"],
        ["7", "config.py is dead code; the app configures itself inline",
         "Two sources of truth for secrets and database settings"],
    ],
    widths=[0.3, 3.3, 2.9],
    body_size=8.5,
)

# ---------------- 9. honesty appendix
h1(doc, "9. Appendix — feature honesty table")

para(doc, "Added to security.md as Appendix A.5. Read this before promising any of these features to a "
          "user, because the gap between the name and the property is where trust is lost.")
add_table(
    doc,
    ["Claimed feature", "Real security property", "Verdict"],
    [
        ["Per-user / per-document DEKs", "Database or host compromise does not yield plaintext", "Real — build first"],
        ["Shamir (k, n) recovery sharding", "No single holder can reconstruct; no single failure loses the key", "Real, if shards are independent and reconstruction is offline"],
        ["Air-gapped HTML vault", "Offline access with no server trust", "Real — bound by passphrase strength"],
        ["Expiring / one-time / password links", "Bounded, revocable distribution", "Real — with atomic counting"],
        ["Key destruction (delete the DEK)", "Ciphertext becomes permanently unreadable", "Real"],
        ["Multi-pass byte overwrite", "—", "Weak — defeated by SSDs, CoW, snapshots, cloud sync"],
        ["Forensic watermarking", "Attribution and deterrence", "Partial — removable by a motivated attacker"],
        ["Canary honey-files", "Detection of access through the vault", "Partial — blind to offline copies"],
        ["LSB steganography", "Hides a payload from casual inspection", "Weak — detectable; deniability defeated by server metadata"],
        ["VDF time-capsules", "Delays decryption of a puzzle", "Speculative — no protection against key holders or coercion"],
        ["Shoulder-surfer blur", "Reduces casual over-the-shoulder reading", "Cosmetic"],
        ["Webcam glance detection", "—", "Not recommended — unreliable and privacy-hostile"],
    ],
    widths=[1.8, 2.3, 2.4],
    body_size=8,
)

# ---------------- 10. references
h1(doc, "10. References and next steps")

add_table(
    doc,
    ["File", "Contents"],
    [
        ["README.md", "Status legend, implemented vs planned features, routes, data-model additions, "
                      "the six core feature specs, the advanced catalogue, the implementation matrix"],
        ["security.md", "Full hardening guide plus Appendix A on coercion, insider and leak threats and "
                        "the feature honesty table"],
        ["Feature Updates & Roadmap (this document)", "Reviewable summary of the same material with the "
                                                      "delivery phasing"],
    ],
    widths=[1.9, 4.6],
    body_size=9,
)

h2(doc, "Recommended next slice")
para(doc, "Trash with 30-day retention plus an owner-scoped replacement for /uploads/<filename>. Both are "
          "small, self-contained, and together they remove a live IDOR while adding a requested feature. "
          "Nothing in Phases 2–4 should start before the Phase 0 items in Section 8.2 are closed.")

para(doc, "Prepared 20 November 2026. No application code was modified in this cycle.", italic=True,
     color=MUTED, size=9, space_before=10)

doc.save(OUT)
print("wrote", OUT)
