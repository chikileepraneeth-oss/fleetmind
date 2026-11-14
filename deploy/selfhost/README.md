# The FleetMind demo on your own website

The demo is a small Python server with a live connection (WebSocket) to every visitor.
It can't run on static hosting (GitHub Pages, Netlify, Wix, WordPress.com, shared hosting),
but it can be **shown** there. Pick the way that fits your website.

The demo is always the **public demo**: one shared warehouse, no MCP tools, no AI, no API keys.

---

## Way A: your website is static or a website builder

Run the demo somewhere that runs Docker (free: Hugging Face Spaces, see the main README,
"Put the public demo online"), then show it inside a page of your website:

```html
<iframe src="https://<your-name>-fleetmind.hf.space/3d"
        style="width:100%; height:720px; border:0; border-radius:12px"
        title="FleetMind live demo" loading="lazy"></iframe>
<p><a href="https://<your-name>-fleetmind.hf.space" target="_blank" rel="noopener">Open the full demo</a></p>
```

Use `/3d` for the 3D view or `/` for the 2D map with the controls.

---

## Way B: you have a server (a VPS or your own machine)

You need: a server with Docker, your domain, and ports 80 and 443 open.
Cost: a small VPS is a few euros a month (1 CPU, 1–2 GB RAM is enough).

1. **DNS:** at your domain provider, add an `A` record: `demo` -> your server's IP address
   (so `demo.yoursite.com` points at the server). Wait until it works: `ping demo.yoursite.com`.
2. **Get the code on the server:**
   ```bash
   git clone https://github.com/chikileepraneeth-oss/fleetmind.git
   cd fleetmind/deploy/selfhost
   ```
3. **Settings:** create `deploy/selfhost/.env` (git ignores it):
   ```
   DEMO_DOMAIN=demo.yoursite.com
   EMBED_FROM=https://www.yoursite.com
   ```
   `EMBED_FROM` is your website: only it may show the demo inside its pages.
4. **Start:**
   ```bash
   docker compose up -d --build
   ```
   The first build takes a few minutes. Caddy gets the HTTPS certificate by itself.
5. **Open** `https://demo.yoursite.com`, and the 3D view at `https://demo.yoursite.com/3d`.
   To show it inside a page of your website, use the `<iframe>` from way A with your domain.

**Update** after new commits: `git pull && docker compose up -d --build`.
**Logs:** `docker compose logs -f`. **Stop:** `docker compose down`.

What is checked (on a test machine with `DEMO_DOMAIN=localhost`): HTTPS works, http goes to
https, the live WebSocket works through Caddy, `/mcp` answers 404, the AI is off, and only the
`EMBED_FROM` website may show the demo in an iframe.

**Safety:** only ports 80 and 443 are open; the demo container is reachable only through Caddy,
runs as a normal user, is limited to 1 CPU and 1 GB RAM, and contains no `.env` or keys.
Don't put an `ANTHROPIC_API_KEY` on this server: the public demo never needs it.
