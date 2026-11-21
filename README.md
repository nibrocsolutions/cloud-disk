# Cloud Disk

A phone page for a portable drive plugged into a Raspberry Pi. Browse the photos and other files, save a batch from an iPhone into a new folder named with the date and time, and organize with new folders, renames, moves, and copies.

Finished files stay on the drive. The app has no way to delete them.

It is the same kind of app as Meow and Dolf: one Python container, SQLite for the album list, and a page served by that container. The page itself is its own layout.

Cloud Disk listens on **port 8050**. The other apps already use 8000 (government-tracker), 8081 (math-facts and trip-planner), 8083 (health-tracker), 8085 (wifi-monitor), 8088 (kinney-aviation), 8092 (dolf-tracker), 8094 (meow), and 8096 (castle).

## Quick start (Raspberry Pi 4, 64-bit Pi OS Lite)

The Seagate drive has to be mounted on the Pi before the container starts. If Docker starts first, it creates an empty folder at the mount path and the photos never show up.

Plug the drive into a blue USB 3 port. If it clicks or drops off the bus, use a powered hub. A Pi 4 shares a limited USB power budget.

```bash
sudo apt-get update
sudo apt-get install -y exfatprogs ntfs-3g
lsblk -f
```

Note the UUID and the filesystem (`exfat` or `ntfs`). Then:

```bash
sudo mkdir -p /mnt/seagate
```

For exFAT, add this to `/etc/fstab` (use the UUID from `lsblk -f`):

```
UUID=XXXX-XXXX  /mnt/seagate  exfat  defaults,uid=1000,gid=1000,umask=000,nofail,x-systemd.device-timeout=10  0  0
```

For NTFS:

```
UUID=XXXX  /mnt/seagate  ntfs-3g  defaults,uid=1000,gid=1000,umask=000,nofail,x-systemd.device-timeout=10  0  0
```

`nofail` lets the Pi boot when the drive is unplugged. `umask=000` lets the container write. Mount it and check that the photos are there:

```bash
sudo mount -a
ls /mnt/seagate
df -h /mnt/seagate
```

`df` should show about 2 TB, not the SD card. Then:

```bash
git clone https://github.com/nibrocsolutions/cloud-disk.git
cd cloud-disk
cp .env.example .env
docker compose up -d --build
```

`.env` sets `DISK_MOUNT=/mnt/seagate`. Change it if you mounted the drive somewhere else.

On the phone, join the same Wi-Fi as the Pi and open `http://<pi-ip>:8050`.

Add it to the home screen (Safari: Share → Add to Home Screen) and it opens like an app.

### Useful commands

```bash
docker compose logs -f
git pull && docker compose up -d --build
docker compose down
```

`docker compose down` keeps the `cloud-disk-data` volume (the album list and preview cache). It does not change the portable drive. Add `-v` only when you mean to erase the album list. That still does not delete files on the drive.

If writes fail with a permission error, the mount owner and the container user do not match. Either keep `umask=000`, or set `user: "1000:1000"` in `docker-compose.yml` to the id that owns the mount.

## What you can do

- Browse folders, photos, videos, and other files
- Search by file name
- Open a photo. HEIC pictures from an iPhone are shown as a preview. The file on the drive is left as it is
- Play a video the phone can play, and open anything else
- Upload photos and videos. Each save creates `Uploads/YYYY-MM-DD_HH-MM-SS` using the Pi's `APP_TZ` (Eastern Time by default)
- Add another group of photos to the same folder, or start a new folder for the next batch
- Create folders, rename, move, and copy
- Keep albums. An album is a list stored on the Pi. Adding or removing a photo there does not change the file

A name that already exists is kept. The new file is saved beside it as `name (2)`. Nothing is replaced.

## What you cannot do

There is no delete. There is no trash. A finished file is never removed by this app. An upload that stops halfway is discarded so a half-written file is not left behind. That cleanup only applies to the app's own unfinished files, the ones whose names end in `.cloud-disk-part`.

## Uploading from an iPhone

A website cannot open the camera roll by itself. Tap **Photos & videos**, then Select, then the pictures. You can select a large group. If the picker stops you, save that group and add another to the same folder.

Leave this page open until the save finishes. The phone is asked to stay awake.

iOS sometimes converts HEIC photos to JPEG when the picker is limited to pictures. **Original files** asks for the files as they are. Use that when you want the camera's HEIC and MOV files unchanged.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p data disk
DATA_DIR=./data DISK_ROOT=./disk EXPECT_MIN_BYTES=0 uvicorn app.main:app --reload --host 0.0.0.0 --port 8050
```

Put a few photos in `disk/` and open `http://127.0.0.1:8050`.

Tests:

```bash
pytest
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET, POST | `/api/session` | See whether the access code is set, or unlock |
| POST | `/api/session/lock` | Lock again |
| GET | `/api/status` | Free space and whether the drive is mounted |
| GET | `/api/browse` | One folder |
| GET | `/api/search` | File names |
| GET | `/api/file` | The original file |
| GET | `/api/media` | A JPEG preview |
| POST | `/api/uploads/batches` | Start `Uploads/date_time` |
| POST | `/api/uploads/batches/{id}/files` | Save one file into that folder |
| POST | `/api/folders` | Create a folder |
| POST | `/api/rename` | Rename a file or folder |
| POST | `/api/move` | Move, without replacing anything |
| POST | `/api/copy` | Copy, without replacing anything |
| GET, POST | `/api/albums` | Album list |
| POST | `/api/albums/{id}/items` | Add files to an album |
| POST | `/api/albums/{id}/items/detach` | Take files out of an album. The files stay |
| POST | `/api/albums/{id}/remove` | Remove the album list. The files stay |
| GET, POST | `/api/backup/albums` | Download or restore the album list |

There is no delete method.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `./data` locally, `/data` in Docker | Album database and preview cache |
| `DISK_ROOT` | `./disk` locally, `/disk` in Docker | The portable drive inside the container |
| `DISK_MOUNT` | `/mnt/seagate` | Host path published as `/disk` |
| `DISK_LABEL` | `Drive` | Name of the top folder in the page |
| `PORT` | `8050` | HTTP port |
| `APP_TZ` | `America/New_York` | Date and time used in upload folder names |
| `TZ` | `America/New_York` in Docker | Clock inside the container |
| `ACCESS_CODE` | empty | Optional code required before the drive is shown |
| `EXPECT_MIN_BYTES` | 200 GB | Warn when the mounted folder is smaller than the portable drive |
| `DATABASE_URL` | `sqlite:///{DATA_DIR}/cloud-disk.db` | Override the album database |

Set `ACCESS_CODE` in `docker-compose.yml` or `.env` if the Pi is reachable beyond the house. Anyone who can open the address can read and add files when the code is empty.

## Notes

- Keep it on your home network. Do not forward port 8050 to the internet. The phone has to reach the Pi. Away from home, use a VPN you already trust.
- Previews are cached on the Pi, under the Docker volume. The portable drive only receives the files you upload and the folders you create.
- The page does not load fonts or scripts from the internet. After the container is built, the page works with the Pi offline.
- The image is `python:3.12-slim`, so it runs on 64-bit Pi OS and on a normal PC.
- Names that start with a dot stay hidden, including the small files macOS leaves on a drive.
- Video previews need `ffmpeg`, which the image includes. If a video has no preview, it can still be opened.

## Project layout

```
├── Dockerfile
├── docker-compose.yml
├── app/
│   ├── main.py
│   ├── uploads.py
│   ├── organize.py
│   ├── api/routes.py
│   └── static/
└── tests/
```
