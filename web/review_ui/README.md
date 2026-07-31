# Gate 0 review UI

This static, mobile-responsive UI contains presentation and transport logic
only. It talks to the external accessory API under `/v1/review/*`; it does not
import compiler, validator, packet, retrieval, or runtime code.

The accessory service hosts the UI at `/review`, so the Android-safe default is:

```bash
uv run aethersparse serve --host 0.0.0.0 --port 8000
```

Open `http://ACCESSORY_LAN_IP:8000/review` from Android. Every review operation
still goes through the external `/v1/review/*` API.

For separate static hosting, run
`python -m http.server 8081 --directory web/review_ui` and add
`?api=http://ACCESSORY_LAN_IP:8000`.
