import ipaddress
import requests

from utils.cache import system_cache

# Free, no-API-key IP geolocation lookup (ip-api.com's free tier is HTTP-only and allows
# ~45 requests/minute, which is fine for an admin-only panel). Results are cached for a
# full day per IP - an IP's city doesn't change minute to minute, and this keeps repeat
# admin page loads from re-hitting the external API for the same users every time.
GEOIP_CACHE_TTL = 86400
# The very first outbound request from a freshly-started worker thread pays extra latency
# (DNS resolution, TCP/TLS handshake) that later requests on a warm connection don't -
# confirmed live, a cold first call to 8.8.8.8 missed a 1.5s timeout while an immediate
# second call to the same API returned in well under a second. A few seconds is still
# nothing on an admin-only page that loads occasionally, not per visitor.
GEOIP_TIMEOUT = 3


def get_location_from_ip(ip: str) -> str | None:
    """
    Best-effort "City, Country" for a public IP address. Returns None for private/loopback
    addresses (nothing external can geolocate those) or if the lookup fails/times out -
    callers should fall back to their own default display rather than treat None as an error.
    """
    if not ip:
        return None

    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            return None
    except ValueError:
        return None

    cache_key = f"geoip_{ip}"
    cached = system_cache.get(cache_key)
    if cached is not None:
        return cached or None

    try:
        resp = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,city,country"},
            timeout=GEOIP_TIMEOUT,
        )
        data = resp.json()
        if data.get("status") == "success":
            location = ", ".join(filter(None, [data.get("city"), data.get("country")])) or None
            system_cache.set(cache_key, location or "", ttl=GEOIP_CACHE_TTL)
            return location
    except Exception:
        pass

    # Cache the miss too (shorter TTL) so a consistently-unreachable IP doesn't add a
    # ~1.5s delay to every single admin page load it appears on.
    system_cache.set(cache_key, "", ttl=600)
    return None
