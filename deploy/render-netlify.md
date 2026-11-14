# The demo on Render, shown on your Netlify website

- **Render** runs the demo (it can run a server with a live connection).
- **Netlify** serves your website (static pages) and shows the demo inside a page.

Netlify can't run the demo itself: it only serves files, and the demo needs a server
that keeps a WebSocket open to every visitor.

## 1. The demo on Render

1. Merge the work into `main` on GitHub (Render builds from your repository).
2. Make an account at render.com (log in with GitHub; no card needed for the free plan).
3. **New > Blueprint**, pick the `fleetmind` repository. Render reads `render.yaml`:
   a Docker web service `fleetmind-demo` on the free plan.
4. Render asks for **EMBED_FROM**: your Netlify address, e.g. `https://yourname.netlify.app`
   (only that website may show the demo inside its pages). Click **Apply**.
5. Wait for the first build (5–10 minutes). The demo is at
   `https://fleetmind-demo.onrender.com` (Render shows the exact address), the 3D view at `/3d`.

**The free plan:** 0.1 CPU and 512 MB, and it sleeps after 15 minutes without visitors; the
next visit wakes it in about a minute. Measured with those limits: the demo uses about 65 MB and
runs about 5 ticks per second with `FLEETMIND_MESSAGES=off` (set in `render.yaml`).

**Smoother, with the VDA 5050 messages:** the Starter plan ($7 a month, 0.5 CPU, no sleeping).
In Render: the service > Settings > Instance type: Starter, and Environment:
`FLEETMIND_MESSAGES=on`.

**Updates:** every push to `main` builds and deploys again by itself (`autoDeploy`).

## 2. Show it on your Netlify website

Put this where the demo should appear (any HTML page of your site):

```html
<section style="max-width:1100px;margin:auto">
  <h2>FleetMind live</h2>
  <p>A warehouse robot fleet with a central brain. Press Start, pick a scenario or a random day.
     (It may take a minute to wake up.)</p>
  <iframe src="https://fleetmind-demo.onrender.com/3d"
          style="width:100%;height:720px;border:0;border-radius:12px;background:#dfe4ea"
          title="FleetMind live demo" loading="lazy"></iframe>
  <p><a href="https://fleetmind-demo.onrender.com" target="_blank" rel="noopener">Open the 2D map with all controls</a></p>
</section>
```

Then deploy your site on Netlify as usual (push to its repository, or drag the folder into Netlify).

## What visitors can and can't do

They share one warehouse: Start, Pause, scenarios, random days, the 2D and 3D views.
There are no MCP tools, no AI and no API key on Render. Never add an `ANTHROPIC_API_KEY` there.
