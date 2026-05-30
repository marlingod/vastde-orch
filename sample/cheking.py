import os
from dotenv import load_dotenv
from vastpy import VASTClient

load_dotenv()

kwargs = {"address": os.environ["VMS_ADDRESS"], "tenant": "default"}
if os.environ.get("VMS_TOKEN"):
    kwargs["token"] = os.environ["VMS_TOKEN"]
else:
    kwargs["user"] = os.environ["VMS_USER"]
    kwargs["password"] = os.environ["VMS_PASSWORD"]

client = VASTClient(**kwargs)
try:
    tenants = client.tenants.get()
    print(f"Connected. Found {len(tenants)} tenants:")
    for t in tenants[:10]:                                                                                                                                           
        print(f"  - {t.get('name', '?')}")
except Exception as e:                                                                                                                                               
    print(f"FAILED: {e}")       