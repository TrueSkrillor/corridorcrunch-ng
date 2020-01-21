import hmac
from urllib.parse import urlparse
from hashlib import sha256
from django.conf import settings

def get_client_ip(request):
	try:
		x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
		if x_forwarded_for:
			ip = x_forwarded_for.split(",")[0]
		else:
			ip = request.META.get("REMOTE_ADDR")
		return ip
	except Exception:
		return None

def get_dict_value(dict, key, default):
	if dict and key in dict:
		return dict[key]
	return default

# When exporting data, we shouldn't really make hash(ip) public because it's
# too easy to reverse. Use HMAC with SECRET_KEY as a keyed hash, to prevent
# reversing while still being usable as a unique identifier within a single
# exported set of data
def disguise_client_ip(ip):
	key = settings.SECRET_KEY.encode("utf-8")
	encoded_ip = ip.encode("utf-8")
	return hmac.new(key, encoded_ip, sha256).hexdigest()

def is_image_url(url):
	file_ext = url.path.lower().split('.')[-1]
	return file_ext in [ "jpg", "jpeg", "png" ]
