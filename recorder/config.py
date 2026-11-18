"""Settings, read from the environment once per process."""
import os
from pathlib import Path
from urllib.parse import urlsplit


class Settings:
    def __init__(self, **overrides):
        env = {**os.environ, **{k.upper(): str(v) for k, v in overrides.items()}}

        def get(name, default=""):
            return env.get(name) or default

        self.data_dir = Path(get("DATA_DIR", "/data"))
        self.public_url = get("PUBLIC_URL", "https://10.1.9.7:8443").rstrip("/")
        self.box_name = get("BOX_NAME", "aibox1")   # what the apps call this box: "aibox1 · ready"
        self.display_tz = get("DISPLAY_TZ", "Europe/Berlin")
        # LLM on aibox1's own Ollama. The -ctx65536 tag is the one the company
        # brain keeps loaded; passing other options (num_ctx) would load a
        # second copy of the model.
        self.ollama_url = get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
        self.llm_model = get("LLM_MODEL", "qwen3.8:27b-ctx65536")
        self.llm_think = get("LLM_THINK", "0") == "1"
        self.engine = get("ENGINE", "A-iq4")
        self.nemo_url = get("NEMO_URL", "http://engine-nemo:8090").rstrip("/")
        self.qwen_url = get("QWEN_URL", "http://engine-qwen:8091").rstrip("/")
        self.chunk_seconds = int(get("CHUNK_SECONDS", "30"))
        self.max_chunk_bytes = int(get("MAX_CHUNK_MB", "64")) * 1024 * 1024
        self.github_repo = get("GITHUB_REPO", "brainbot-com/indie-recorder-sandbox")
        self.github_token_file = Path(get("GITHUB_TOKEN_FILE", "/secrets/github_token"))
        # agent: approved follow-ups go to the outbox and the Agent Command Center
        # agent (which has GitHub and Drive access) carries them out;
        # direct: this service calls GITHUB_API_URL / DRIVE_API_URL itself (the mocks).
        self.followup_executor = get("FOLLOWUP_EXECUTOR", "agent")
        # Where follow-ups may go: "drive" for now; "drive,github" adds issue proposals.
        self.followup_targets = {t.strip() for t in get("FOLLOWUP_TARGETS", "drive").split(",") if t.strip()}
        # Initial defaults; the app or the web view can change them later (settings table).
        self.storage_default = get("STORAGE_DEFAULT", "local")
        # Base of the signed file links handed to the agent; it downloads from
        # there. Default: this service's own address (VPN clients).
        self.files_base_url = get("FILES_BASE_URL", self.public_url + "/api/v1/files").rstrip("/")
        self.github_api_url = get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.drive_api_url = get("DRIVE_API_URL", "").rstrip("/")
        self.followup_auto = get("FOLLOWUP_AUTO", "0") == "1"
        self.followup_on_done = get("FOLLOWUP_ON_DONE", "1") == "1"
        # The Command Center agent only takes meetings recorded after this
        # (ISO date), so test meetings from before go-live stay untouched.
        self.agent_since = get("AGENT_SUMMARIES_SINCE", "")
        self.drive_log_doc = get("DRIVE_LOG_DOC", "Meeting log")
        self.drive_folder = get("DRIVE_FOLDER", "indie-recorder test")
        # The Android app's pairing link carries the box's CA, so the app trusts this box only.
        self.ca_cert_file = Path(get("CA_CERT_FILE", "/secrets/tls/ca.crt"))
        # The app for phones: Caddy's CA port serves secrets/tls/public, VPN only.
        self.apk_file = Path(get("APK_FILE", "/secrets/tls/public/indie-recorder.apk"))
        self.apk_url = get("APK_URL", f"http://{urlsplit(self.public_url).hostname}:8480/indie-recorder.apk")
        # Where a connect code leads: a page that hands it to the app or the web app. Behind a
        # proxy with a certificate phones trust (brain-tls), no phone needs the box's CA for it.
        self.connect_url = get("CONNECT_URL", self.public_url + "/connect")
        self.webapp_url = get("WEBAPP_URL", self.public_url + "/app/")
        # Encryption at rest (recorder/vault.py) with the keys deploy/make-keys.sh makes once;
        # phones seal their chunks for SEAL_KEY_FILE. ENCRYPT_AT_REST=0 keeps files plain.
        self.encrypt_at_rest = get("ENCRYPT_AT_REST", "1") == "1"
        self.data_key_file = Path(get("DATA_KEY_FILE", "/secrets/data.key"))
        self.seal_key_file = Path(get("SEAL_KEY_FILE", "/secrets/seal.pem"))
        # Plain audio for ffmpeg and the engines during a job: a tmpfs the engines share.
        self.scratch_dir = Path(get("SCRATCH_DIR", "/scratch"))
        self.secure_cookies = get("SECURE_COOKIES", "1") == "1"
        self.worker = get("WORKER", "1") == "1"

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
