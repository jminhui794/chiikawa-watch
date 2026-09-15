"""Generate a VAPID key once. The private key must only go into GitHub Secrets."""
import base64
import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

base = Path(__file__).resolve().parent
private_path = base / '.push-private-key.txt'
if private_path.exists():
    raise SystemExit('Key already exists. Keeping existing phone subscriptions intact.')
key = ec.generate_private_key(ec.SECP256R1())
private = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
with private_path.open('x', encoding='utf-8') as file:
    file.write(base64.urlsafe_b64encode(private).decode().rstrip('='))
(base / 'web' / 'push-config.json').write_text(json.dumps({
    'publicKey': base64.urlsafe_b64encode(public).decode().rstrip('=')
}), encoding='utf-8')
print('Public key saved to web/push-config.json; private key saved to ignored .push-private-key.txt.')
