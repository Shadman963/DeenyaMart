"""Production settings for DeenyaMart (Render-ready)."""
import os
from .base import *  # noqa: F401, F403
from .base import ALLOWED_HOSTS, MIDDLEWARE  # noqa: F401

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Render injects the public hostname of the service automatically.
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
for host in ALLOWED_HOSTS:
    if host and not host.startswith(".") and host not in ("localhost", "127.0.0.1", "testserver"):
        origin = f"https://{host}"
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

# Render terminates TLS at its proxy and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Compressed, cache-busted static files served by WhiteNoise.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Serve user-uploaded / imported media through Django (no separate CDN/bucket configured).
# Point MEDIA_ROOT at a Render persistent disk to keep admin uploads across deploys.
SERVE_MEDIA = os.environ.get("SERVE_MEDIA", "True").lower() in ("true", "1", "yes")
if os.environ.get("MEDIA_ROOT"):
    MEDIA_ROOT = os.environ["MEDIA_ROOT"]

SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
