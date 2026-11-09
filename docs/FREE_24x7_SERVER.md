# Run HARRY 24/7 for free (laptop can be off)

HARRY runs on a free **Oracle Cloud "Always Free"** server. Your laptop is only needed
to set it up, to send updates, and occasionally to log in to Moodle again.

## 1. Create the free server (one time, about 15 minutes)

1. Go to **https://www.oracle.com/cloud/free/** → *Start for free*.
   * Pick a **Home Region** close to Pakistan (e.g. *UAE East (Dubai)*, *Saudi Arabia West (Jeddah)*
     or *India West (Mumbai)*). It can't be changed later.
   * A debit/credit card is needed for identity verification only. Always Free resources are not charged.
2. In the Oracle Cloud console: **☰ Menu → Compute → Instances → Create instance**.
   * **Name:** `harry`
   * **Image:** *Canonical Ubuntu 24.04*
   * **Shape:** *Ampere* → `VM.Standard.A1.Flex`, **1 OCPU, 6 GB memory** (shows "Always Free-eligible").
     If you get *"Out of capacity"*, try another availability domain, try again later, or use
     `VM.Standard.E2.1.Micro` (also free; slower, and HARRY adds swap memory automatically).
   * **Networking:** keep the defaults; *Assign a public IPv4 address* must be **Yes**.
   * **Add SSH keys:** *Generate a key pair for me* → **Save private key** (keep this `.key` file safe).
   * Click **Create** and wait until the instance is *Running*.
3. Copy the **Public IP address** shown on the instance page.

## 2. Put HARRY on the server (one time, about 10 minutes)

On your laptop, in the project folder:

1. Stop HARRY on the laptop (close the START_HARRY window), then get the newest code:
   ```powershell
   git pull origin main
   ```
2. Double-click **`DEPLOY_TO_SERVER.bat`**. The first time, it asks for:
   * the server's public IP,
   * the full path of the `.key` file you downloaded,
   * the user name (press ENTER for `ubuntu`). Answer **yes** if asked to trust the server.

   It uploads HARRY and your `.env`, installs everything and starts HARRY as a background
   service that restarts automatically (also after server reboots).
3. Double-click **`LOGIN_AND_SEND.bat`**. A Moodle window opens; sign in if it asks. Your
   Moodle login is then sent to the server.
4. On WhatsApp, send **`menu`** to your HARRY agent once (the server learns your chat).

That's it. You can turn the laptop off.

## Day to day

| Situation | What to do |
|---|---|
| WhatsApp says **"Moodle login needed"** | Double-click `LOGIN_AND_SEND.bat` on the laptop and sign in |
| You pulled a HARRY update (`git pull origin main`) | Double-click `DEPLOY_TO_SERVER.bat` |
| See what HARRY is doing | Double-click `SERVER_LOG.bat` (Ctrl+C to close) |
| Check from your phone | Send `/status` on WhatsApp |

**Never run `START_HARRY.bat` on the laptop while the server is running.** Two HARRYs with the
same WhatsApp key interrupt each other.

## Good to know

* **How often you need to log in again** depends on the university's Moodle session settings.
  HARRY checks every 30 minutes, which normally keeps the session alive. If your LMS ties a login
  to one internet connection, the login sent from the laptop won't work on the server: HARRY
  then immediately reports "Moodle login needed". If that keeps happening, tell me.
* **Oracle idle rule:** Oracle may reclaim *Always Free* instances that stay almost completely
  idle for 7 days. To avoid that, upgrade the account to **Pay As You Go** (Billing → Upgrade).
  Always Free resources stay free; you only pay if you create paid resources.
* Your `.env`, Moodle login and data live only on your laptop and your own server
  (`~/harry`). The login file is readable only by the server's user.
* To change server details, delete `deploy\server.local` and run `DEPLOY_TO_SERVER.bat` again.
