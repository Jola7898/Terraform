# Running RTVIO Studio as a remote processing server

One GPU PC ("the processing PC") runs RTVIO Studio and all reconstruction.
The website lives on Vercel, so it is always up; it sends everything to the
PC and shows **"Processing server is offline"** whenever the PC is off,
disconnected, or the Studio isn't running.

```
 any browser ──► https://your-app.vercel.app      (static UI, always up)
                    │  API calls, uploads, live view (HTTPS, token sign-in)
                    ▼
               https://studio.yourdomain.com      (Cloudflare Tunnel, your own domain - §1)
            or https://<pc>.<tailnet>.ts.net      (Tailscale Funnel, free, no domain - §1b)
                    │  outbound from the PC: no port forwarding, works behind CGNAT
                    ▼
               processing PC: RTVIO Studio 127.0.0.1:8080 ──► VGGT on the GPU
```

The Vercel page is the same UI the Studio serves locally
(`rtvio/src/rtvio/studio/web/`); `deploy/vercel/build.mjs` copies it and
points it at the PC with one environment variable. Nothing about the
pipeline runs on Vercel.

## What works through the website

| | via Vercel + Cloudflare |
|---|---|
| Upload a video from any device, and see progress | ✅ chunked (32 MB pieces), resumes after a dropped connection |
| Record a video with a phone's camera in the browser and reconstruct | ✅ "Record with camera" |
| Job queue, GPU graph, logs, cancel/delete | ✅ |
| 3D viewer, downloads (.ply, report), session .zip export/import | ✅ |
| "Watch live" reconstruction viewer | ✅ proxied at `/viz/` |
| Live phone/drone preview and Start/Stop recording | ✅ when that device is connected to the PC (below) |
| **Phone app streaming to the PC** | ⚠️ raw TCP on port 5555, which Cloudflare cannot carry: use the app on the PC's WiFi, via Tailscale (§5), or record offline in the app → export .zip → **Import session** on the website |
| **Drone video + telemetry** | ⚠️ the PC pulls these from the drone's IP, so the drone must be reachable from the PC: at the PC's location, or via Tailscale (§5); or record on a field laptop running the Studio → **Export .zip** → import on the website |

## 1. Cloudflare Tunnel on the processing PC (once)

1. Add your domain to Cloudflare (free plan) and switch the domain's
   nameservers to Cloudflare's at your registrar. Wait until the dashboard
   shows the domain as **Active**.
2. Cloudflare dashboard → **Zero Trust** → **Networks → Tunnels → Create a
   tunnel** → *Cloudflared* → name it `rtvio`.
3. It shows an install command for Windows. On the PC, install cloudflared
   (`winget install --id Cloudflare.cloudflared`), then run the shown
   `cloudflared.exe service install <token>` in an **Administrator**
   PowerShell. The tunnel now runs as a Windows service and starts at boot.
4. In the tunnel's **Public Hostname** tab, add:
   - Subdomain `studio`, domain `yourdomain.com`
   - Service: **HTTP**, URL **`127.0.0.1:8080`** (write `127.0.0.1`, not
     `localhost`: on Windows `localhost` may resolve to IPv6 `::1`, where the
     Studio doesn't listen)

`https://studio.yourdomain.com` now reaches the Studio whenever the PC is on.

## 1b. Or, with no domain: Tailscale Funnel

Funnel gives the PC a free, permanent public address,
`https://<pc-name>.<tailnet>.ts.net`. Only the PC runs Tailscale; people
opening the Vercel site install nothing.

1. Install Tailscale on the PC (<https://tailscale.com/download/windows>)
   and sign in. In the admin console, open the PC's menu and choose
   **Disable key expiry** so it stays online indefinitely.
2. Start the Studio with `-Funnel` (§3). The first time, `tailscale funnel`
   prints a link to enable HTTPS certificates and Funnel for your tailnet.
   Open it, approve, and the script continues. It then prints the address:

   ```
   Tailscale Funnel: https://gpu-pc.tail1234.ts.net
     -> on Vercel, set RTVIO_API_URL = https://gpu-pc.tail1234.ts.net (then redeploy)
   ```
3. Use that address as `RTVIO_API_URL` in §2.

`-Funnel` is remembered, and the Funnel setting persists across reboots, so
later runs need no arguments. `-NoFunnel` takes the PC off the internet
again. The address changes only if you rename the PC in Tailscale.
Tailscale applies bandwidth limits to Funnel traffic: controlling jobs
and uploading clips is fine, but long live previews may be slow. If that
matters, move to §1 later: that only means changing `RTVIO_API_URL` on
Vercel and redeploying.

## 2. Deploy the website on Vercel (once)

1. Push this repo to GitHub, then in Vercel choose **Add New → Project** and
   import it. Leave **Root Directory** as the repo root; `vercel.json`
   already sets the build command (`node deploy/vercel/build.mjs`) and the
   output directory (`deploy/vercel/dist`).
2. **Environment Variables** → add `RTVIO_API_URL` =
   `https://studio.yourdomain.com`, or your Funnel address from §1b (no
   trailing slash; must be https).
3. Deploy. Note the site's URL, e.g. `https://rtvio-studio.vercel.app`. If
   you add a custom domain to the Vercel project, use that URL instead.

Changing `RTVIO_API_URL` later needs a redeploy (Deployments → ⋯ →
Redeploy), because it is written into `static/config.js` at build time.

## 3. Start the Studio on the processing PC

```powershell
cd rtvio
powershell -ExecutionPolicy Bypass -File tools\start_studio_server.ps1 -AllowOrigin https://rtvio-studio.vercel.app
# using Tailscale Funnel instead of a Cloudflare tunnel (§1b): add -Funnel
```

- The first run asks for the Studio password and saves it
  (`RTVIO_STUDIO_PASSWORD`), along with the allowed site
  (`RTVIO_STUDIO_CORS_ORIGINS`). Later runs need no arguments.
- `-AllowOrigin` must match the Vercel URL exactly, with no trailing slash:
  only that site's pages may call the PC's API. Add Vercel preview URLs
  comma-separated if you use them (wildcards such as
  `https://rtvio-studio-*.vercel.app` work too).
- The Studio refuses to start with an allowed origin but no password.

To keep the server available: set Windows to never sleep when plugged in
(*Settings → System → Power*). To start the Studio at logon, create a Task
Scheduler task that runs `powershell.exe` with arguments
`-ExecutionPolicy Bypass -File "<path>\rtvio\tools\start_studio_server.ps1"`.
The tunnel is already a service.

Equivalent command without the script:

```powershell
$env:RTVIO_STUDIO_PASSWORD = "..."
python -m rtvio.studio --cors-origin https://rtvio-studio.vercel.app
```

## 4. Using it

Open the Vercel URL on any device:

- **PC off / offline / Studio not running** → *"Processing server is offline"*.
  The page re-checks every 10 s and connects on its own as soon as the PC
  is back. If the PC goes away while you're using the page, the same
  screen comes back after three missed updates (~3 s).
- **PC online** → sign in with the Studio password. The browser keeps you
  signed in (a token in local storage) until the password changes.
- **Video file** card → *Upload video & reconstruct*, or *Record with
  camera* on a phone. The clip is uploaded in pieces with a progress
  readout and queued on the PC's GPU. Uploads have no GPS track, so they are
  reconstructed vision-only.

## 4b. Phone app through the tunnel (no Tailscale on the phone)

The Studio relays the phone app's stream over a WebSocket at `/ws/phone`, so
it goes through the same HTTPS address as the website (Funnel or Cloudflare),
on any network including mobile data. In the app's Settings set **Server IP**
to the public address (`https://<pc>.<tailnet>.ts.net`, or your
`https://studio.yourdomain.com`) and **Studio password** to the Studio's
password, then Test connection. A bare IP still uses raw TCP (LAN/Tailscale)
as before. Live streaming is bandwidth-limited by the tunnel: lower the
resolution or JPEG quality if it stutters.

## 5. Phone app and drone from anywhere: add Tailscale (optional)

Cloudflare only carries web traffic. For the **phone app's live stream**
(TCP :5555) or a **drone in the field**, put the devices on one private
network with Tailscale. It runs alongside the Vercel setup; the Vercel page
still controls everything.

1. Install Tailscale (<https://tailscale.com/download>) on the processing
   PC and sign in. Disable key expiry for the PC in the admin console. Note
   its address: `tailscale ip -4` (e.g. `100.101.102.103`).
2. **Phone app:** install Tailscale on the phone, then set the app's
   Settings → **Server IP** to the PC's `100.x` address, port `5555`.
3. **Drone:** a field laptop joins the drone's WiFi and routes it:
   `tailscale up --advertise-routes=192.168.144.0/24` (the drone's subnet).
   Approve the route in the admin console. Enable *Use Tailscale subnets*
   on the PC (or `tailscale up --accept-routes`). Then enter the drone's
   normal IP in the page's *Drone connection* card. Remote RTSP adds
   latency, so re-measure `drone.video_delay_ms` before a georeferenced
   (Outdoor) flight.

The start script's firewall rules allow ports 8080 and 5555 from the LAN
and from Tailscale (`100.64.0.0/10`) only; run it once as Administrator to
create them.

## Limits and security

- **Password:** it protects a machine that runs GPU jobs and serves your
  data to the internet. Use a long one.
- **Token in URLs:** images, downloads and the live viewer can't send an
  Authorization header, so the Vercel page appends `?token=` to those
  URLs. Treat a copied download link as a credential. Changing the password
  invalidates all tokens.
- **Never port-forward** 5555 (phone) or 8767 (raw viewer): they have no
  password. The website reaches the viewer through the Studio's
  authenticated `/viz/`.
- **Cloudflare limits:** each request can be at most 100 MB (uploads are
  chunked to stay under it). A response that sends nothing for 100 s fails
  with error 524: exporting a very large session as .zip can hit this,
  because the zip is built before the download starts. Export those on
  the PC itself.
- **Bandwidth:** the live preview is roughly 12 JPEG frames/s. Close the
  tab or switch source when you're on metered data.
- The live 3D viewer loads three.js from `cdn.jsdelivr.net`.

## Troubleshooting

| symptom | check |
|---|---|
| Always "offline" | `<RTVIO_API_URL>/api/health` in a browser should show `{"ok": true, ...}`. If it doesn't, check whether the Studio is running. For Cloudflare, also check the `cloudflared` service (`Get-Service cloudflared`) and that the public hostname points to `127.0.0.1:8080`. For Funnel, check `tailscale funnel status` (it should show `Funnel on` → `http://127.0.0.1:8080`). |
| `/api/health` works but the Vercel page says offline | CORS: the Studio's startup output must list your exact Vercel URL under "remote UI allowed from". Also check `RTVIO_API_URL` on Vercel (https, no trailing slash), then redeploy. |
| Sign-in says wrong password | The password is `RTVIO_STUDIO_PASSWORD` on the PC; after changing it, restart the Studio. |
| Upload stalls or fails | It retries each chunk 5 times; start the upload again if the connection stays down. Partial uploads are deleted after 24 h. |
| "Watch live" is blank | Only while a job is running; the device needs internet access to `cdn.jsdelivr.net`. |
