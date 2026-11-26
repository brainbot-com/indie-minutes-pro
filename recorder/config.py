"""Settings, read from the environment once per process."""
import os
from pathlib import Path

from . import urls

# the Android app's rolling release on GitHub (android/publish-github.sh replaces its file)
GITHUB_APK = "https://github.com/brainbot-com/indie-minutes-pro/releases/download/android/minutes-pro.apk"


class Settings:
    def __init__(self, **overrides):
        env = {**os.environ, **{k.upper(): str(v) for k, v in overrides.items()}}

        def get(name, default=""):
            return env.get(name) or default

        self.data_dir = Path(get("DATA_DIR", "/data"))
        # PUBLIC_URL=auto: whatever address requests come in on (an Indie Box gateway)
        self._public_url = get("PUBLIC_URL", "auto").rstrip("/")
        self._urls = {k: get(k).rstrip("/") for k in ("FILES_BASE_URL", "APK_URL", "CONNECT_URL", "WEBAPP_URL",
                                                       "BROWSER_URL", "GOOGLE_REDIRECT_URI")}
        self.box_name = get("BOX_NAME", "Box")      # what the apps call this box: "aibox1 · ready"
        # how phones reach the box, for the connect steps: vpn (aibox1) or lan (an Indie Box at home)
        self.phone_reach = get("PHONE_REACH", "lan")
        self.display_tz = get("DISPLAY_TZ", "Europe/Berlin")
        # LLM on aibox1's own Ollama. The -ctx65536 tag is the one the company
        # brain keeps loaded; passing other options (num_ctx) would load a
        # second copy of the model.
        self.ollama_url = get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
        self.llm_model = get("LLM_MODEL", "qwen3.8:27b")
        self.llm_think = get("LLM_THINK", "0") == "1"
        # tokens the model reads at once; 0: what an Indie Box says about it, else 64k
        self.llm_context = int(get("LLM_CONTEXT", "0"))
        # send it as num_ctx too (an Ollama of one's own); never on a shared one
        self.llm_send_context = get("LLM_SEND_CONTEXT", "0") == "1"
        # an Indie Box may offer a remote provider's model under a local-looking name:
        # transcripts would leave the box, so only with LLM_REMOTE_OK=1
        self.llm_remote_ok = get("LLM_REMOTE_OK", "0") == "1"
        self.engine = get("ENGINE", "A-iq4")
        self.nemo_url = get("NEMO_URL", "http://engine-nemo:8090").rstrip("/")
        self.qwen_url = get("QWEN_URL", "off").rstrip("/")          # engine B: aibox1 only
        # accent hints for naming speakers (engines/accent, CPU), English meetings only
        self.accent = get("ACCENT", "0") == "1"
        self.accent_url = get("ACCENT_URL", "http://engine-accent:8092").rstrip("/")
        self.chunk_seconds = int(get("CHUNK_SECONDS", "30"))
        self.max_chunk_bytes = int(get("MAX_CHUNK_MB", "64")) * 1024 * 1024
        self.github_repo = get("GITHUB_REPO", "")
        self.github_token_file = Path(get("GITHUB_TOKEN_FILE", "/secrets/github_token"))
        # agent: approved follow-ups go to the outbox and the Agent Command Center
        # agent (which has GitHub and Drive access) carries them out;
        # direct: this service calls GITHUB_API_URL / DRIVE_API_URL itself (the mocks).
        self.followup_executor = get("FOLLOWUP_EXECUTOR", "agent")
        # Where follow-ups may go: "drive" for now; "drive,github" adds issue proposals.
        self.followup_targets = {t.strip() for t in get("FOLLOWUP_TARGETS", "drive").split(",") if t.strip()}
        # Initial defaults; the app or the web view can change them later (settings table).
        self.storage_default = get("STORAGE_DEFAULT", "local")
        self.github_api_url = get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.drive_api_url = get("DRIVE_API_URL", "").rstrip("/")
        self.followup_auto = get("FOLLOWUP_AUTO", "0") == "1"
        self.followup_on_done = get("FOLLOWUP_ON_DONE", "1") == "1"
        # The Command Center agent only takes meetings recorded after this
        # (ISO date), so test meetings from before go-live stay untouched.
        self.agent_since = get("AGENT_SUMMARIES_SINCE", "")
        # Whether meetings can go to Google Drive here at all: with Google's sign-in set up
        # (GOOGLE_OAUTH_FILE) always; DRIVE=0 hides it where neither that nor an agent with
        # Drive tools carries it out (an Indie Box without a Google client).
        self.drive = get("DRIVE", "0") == "1"
        self.drive_log_doc = get("DRIVE_LOG_DOC", "Meeting log")
        self.drive_folder = get("DRIVE_FOLDER", "Minutes Pro")
        # The Android app's pairing link carries the box's CA, so the app trusts this box only.
        self.ca_cert_file = Path(get("CA_CERT_FILE", "/secrets/tls/ca.crt"))
        # The app for phones, served by this service (APK_URL can point elsewhere).
        self.apk_file = Path(get("APK_FILE", "/secrets/tls/public/indie-recorder.apk"))
        # Google Drive, put there by the recorder for each person who connects it (gdrive.py)
        self.google_oauth_file = Path(get("GOOGLE_OAUTH_FILE", "/secrets/google_oauth.json"))
        self.google_auth_url = get("GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
        self.google_token_url = get("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
        self.google_revoke_url = get("GOOGLE_REVOKE_URL", "https://oauth2.googleapis.com/revoke")
        self.google_api_url = get("GOOGLE_API_URL", "https://www.googleapis.com").rstrip("/")
        # Encryption at rest (recorder/vault.py) with the keys deploy/make-keys.sh makes once;
        # phones seal their chunks for SEAL_KEY_FILE. ENCRYPT_AT_REST=0 keeps files plain.
        self.encrypt_at_rest = get("ENCRYPT_AT_REST", "1") == "1"
        self.data_key_file = Path(get("DATA_KEY_FILE", "/secrets/data.key"))
        # or the key itself (an Indie Box keeps it in its install form): base64url or hex
        self.data_key = get("DATA_KEY")
        self.seal_key_file = Path(get("SEAL_KEY_FILE", "/secrets/seal.pem"))
        # Plain audio for ffmpeg and the engines during a job: a tmpfs the engines share
        # (compose mounts it here; the engines take only paths under /data).
        self.scratch_dir = Path(get("SCRATCH_DIR", str(self.data_dir / "scratch")))
        # one GPU job at a time: the engines and the recorder's own LLM calls share this lock
        # file (on a volume they all see: data/ on aibox1, the scratch space on an Indie Box)
        self.gpu_lock = Path(get("GPU_LOCK", str(self.data_dir / ".gpu.lock")))
        self.secure_cookies = get("SECURE_COOKIES", "1") == "1"
        # Signing in with a password, as the box's owner of the recorder (an Indie Box asks
        # for it at install); without one, only tokens sign in.
        self.owner_password = get("OWNER_PASSWORD")
        self.owner_name = get("OWNER_NAME")
        # who answers privacy questions, shown on /privacy (the operator's own contact)
        self.privacy_contact = get("PRIVACY_CONTACT")
        self.worker = get("WORKER", "1") == "1"

    # -- where people and agents reach this service ------------------------------

    @property
    def public_url(self):
        return urls.current() if self._public_url == "auto" else self._public_url

    @property
    def files_base_url(self):
        """Base of the signed file links handed to agents; they download from there."""
        return self._urls["FILES_BASE_URL"] or self.public_url + "/api/v1/files"

    @property
    def apk_url(self):
        """Where phones get the Android app: APK_URL, else the recorder's own copy (APK_FILE),
        else the latest release on GitHub."""
        if self._urls["APK_URL"]:
            return self._urls["APK_URL"]
        if self.apk_file.exists():
            return self.public_url + "/indie-recorder.apk"
        return GITHUB_APK

    @property
    def connect_url(self):
        """Where a connect code leads: a page that hands it to the app or the web app."""
        return self._urls["CONNECT_URL"] or self.public_url + "/connect"

    @property
    def webapp_url(self):
        return self._urls["WEBAPP_URL"] or self.public_url + "/app/"

    @property
    def browser_url(self):
        """Where people's browsers reach the recorder, for pages like Google's sign-in."""
        return self._urls["BROWSER_URL"] or self.connect_url.rsplit("/connect", 1)[0]

    @property
    def google_redirect_uri(self):
        return self._urls["GOOGLE_REDIRECT_URI"] or self.browser_url + "/oauth/google/callback"

    @property
    def db_path(self):
        return self.data_dir / "index.sqlite"

    @property
    def meetings_dir(self):
        return self.data_dir / "meetings"

    @property
    def uploads_dir(self):
        return self.data_dir / "uploads"

    @property
    def outbox_dir(self):
        return self.data_dir / "outbox"

    def ensure_dirs(self):
        for d in (self.data_dir, self.meetings_dir, self.uploads_dir, self.outbox_dir):
            d.mkdir(parents=True, exist_ok=True)
