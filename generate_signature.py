import hmac, hashlib, base64

secret = b"xCCArrmll5cCxaf3YXlWuM30trr"
body = b'{"id":123,"status":"created","total":100.00}'

mac = hmac.new(secret, body, hashlib.sha256).digest()
signature = base64.b64encode(mac).decode()
print(signature)
