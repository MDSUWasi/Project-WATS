Here's the enhanced version. Replace your USER_MANUAL.md with this:

---

# Project WATS — User Manual

## Table of Contents

1. [What is Project WATS?](#what-is-project-wats)
2. [Quick Start](#quick-start)
3. [The Interface](#the-interface)
4. [Loading Targets](#loading-targets)
5. [The Sky View](#the-sky-view)
6. [Blink Comparator](#blink-comparator)
7. [Detector Bands](#detector-bands)
8. [Spectrum Explorer](#spectrum-explorer)
9. [Light Curves](#light-curves)
10. [Source Extraction](#source-extraction)
11. [Catalog Overlays](#catalog-overlays)
12. [Ghost Trail Difference](#ghost-trail-difference)
13. [Moving Object Detection](#moving-object-detection)
14. [Annotations](#annotations)
15. [Collections](#collections)
16. [Watchlist & Alerts](#watchlist--alerts)
17. [Discovery Cards](#discovery-cards)
18. [Display Settings](#display-settings)
19. [Shareable URLs](#shareable-urls)
20. [API Reference](#api-reference)
21. [Troubleshooting](#troubleshooting)
22. [Data Credits](#data-credits)

---

## What is Project WATS?

Project WATS (Where Astronomical Targets Shimmer) is a free, no-login astronomy workbench for exploring SPHEREx infrared data. It turns raw SPHEREx spectral images into an interactive research tool.

**No accounts. No API keys. No sign-in.** Just open the site and start exploring.

### What SPHEREx is

SPHEREx (Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer) is a NASA near-infrared space telescope. It maps the entire sky in 102 spectral bands. Project WATS lets you explore SPHEREx data interactively — blink between visits, extract spectra, detect moving objects, and more.

### What you can do

- Compare SPHEREx images across time to find variable stars and moving objects
- Extract per-pixel spectra using SPHEREx's built-in wavelength calibration
- Plot brightness over time (light curves)
- Detect and classify moving objects automatically
- Annotate features of interest and share them with the community
- Save targets to collections and set up alerts for new observations
- Export data in multiple formats
- Share your views via bookmarkable URLs

---

## Quick Start

### Step 1: Open the app

Navigate to the deployed URL. The homepage appears.

### Step 2: Pick a preset

The sidebar lists 5 preset targets. Click any one to load it instantly:

| Preset | Object Type | Why It's Interesting |
|---|---|---|
| WISE 0855 | Brown dwarf | One of the closest known brown dwarfs; first SPHEREx deep field |
| Barnard's Star | Nearby red dwarf | High proper motion; one of the fastest-moving stars |
| Epsilon Indi B | Brown dwarf | Well-studied benchmark brown dwarf |
| RW Aurigae | Young star | T Tauri star with variable accretion |
| R Draconis | AGB star | Long-period variable; dramatic brightness changes |

Preset clicks load instantly because rendered frames are baked into the app. No waiting, no downloads.

### Step 3: Explore

Once a target loads, use the sidebar to switch between views: Sky, Spectrum, Lightcurve, Sources, Quality, Difference, Analysis, Overlays, Annotations, Collections, Watchlist, and Export.

### Step 4: Try custom coordinates

Enter any RA/Dec in the Sky view query form to explore an arbitrary patch of sky. The first load queries IRSA and downloads cutouts (2-5 minutes). Repeat loads are cached and instant.

---

## The Interface

### Layout

```
┌─────────────────────────────────────────────────────┐
│  Menu  │  Project WATS          │  Display Mode    │  ← Top bar
├──────────┼─────────────────────────────────────────┤
│          │                                         │
│ Sidebar  │         Main Canvas                     │
│          │                                         │
│ (250px)  │       (rest of width)                   │
│          │                                         │
├──────────┴─────────────────────────────────────────┤
│  Frame Selector  │  Stretch  │  Playback  │  Refresh │  ← Bottom bar
└─────────────────────────────────────────────────────┘
```

### Sidebar

The sidebar is your navigation hub. It contains collapsible sections:

**Data Views**
- **Sky** — Main blink comparator
- **Spectrum** — Per-pixel spectral extraction
- **Lightcurve** — Brightness over time
- **Sources** — Detected point sources
- **Quality** — Data quality metrics
- **Difference** — Frame-to-frame ghost trail
- **Analysis** — Moving object detection
- **Overlays** — Catalog source overlays

**Community**
- **Annotations** — Your drawn markings
- **Collections** — Saved targets
- **Watchlist** — Saved coordinates with alerts

**Export**
- **Export** — Download data in multiple formats

### Main canvas

The main area renders the currently selected view. Most views display PNG frames on an HTML5 canvas with zoom and pan controls.

### Top bar

| Element | Description |
|---|---|
| Menu toggle | Opens/closes sidebar on small screens |
| Project WATS logo | Returns to homepage |
| Release selector | Chooses SPHEREx DR1 / QR3 / QR2 |
| Display mode | Cycles Default / IR grayscale / High contrast |

### Bottom bar

| Element | Description |
|---|---|
| Frame selector | Slider across visits (epochs) |
| Stretch control | Adjust image contrast |
| Playback controls | Play/pause animation across visits |
| Refresh data | Re-queries IRSA for new data |

---

## Loading Targets

### Presets (instant, no waiting)

Click any preset in the sidebar. The app loads pre-rendered frames with zero delay. Presets are the recommended starting point for first-time users.

### Custom coordinates

1. Click **Sky** in the sidebar
2. Fill in the query form:

| Field | Format | Example | Default |
|---|---|---|---|
| RA | Decimal degrees or sexagesimal | `262.93` or `17h31m12s` | — |
| Dec | Decimal degrees or sexagesimal | `-26.89` or `-26d53m30s` | — |
| FOV | Degrees | `0.15` | 0.15 |
| Max frames | Integer | `6` | 6 |
| Name | Text label | `My Target` | — |
| Release | DR1, QR3, or QR2 | `dr1` | dr1 |

3. Click **Load frames**

### Understanding the loading process

When you load a new target for the first time:

1. **Query** — The app queries IRSA's TAP service for SPHEREx images covering your coordinates
2. **Download** — It downloads raw cutout files (MEF format) from IRSA's IBE service
3. **Reproject** — Each cutout is reprojected onto a common north-up grid using astropy WCS
4. **Render** — Frames are rendered as PNGs in multiple views (gray, color, bands, etc.)
5. **Cache** — Results are stored so repeat loads are instant

A progress indicator shows the current stage. The first load takes 2-5 minutes depending on IRSA load and your connection.

### Repeat loads (cached)

Loading the same coordinates again uses the cached manifest — instant. The cache key includes coordinates, FOV, max frames, and release, so different settings produce different cached results.

### Refresh data

The **Refresh data** button forces a fresh IRSA query. This is useful when:
- You suspect the cached data is stale
- New SPHEREx visits have occurred since the last load
- You want to see if IRSA has new data for this field

---

## The Sky View

The Sky view is the main blink comparator. It shows the target field as SPHEREx sees it.

### Frame types

| Button | Description | Best For |
|---|---|---|
| Gray | Average of all detector bands, grayscale | General viewing, blink comparison |
| Color | Composite color from detector bands | Visual inspection |
| Sub | Background-subtracted gray | Removing sky background |
| Color Sub | Background-subtracted color | Clean color images |
| Band D1-D6 | Individual detector images | Per-detector analysis |

### Canvas controls

| Action | How |
|---|---|
| Zoom | Mouse wheel or pinch gesture |
| Pan | Click and drag |
| Zoom to fit | Double-click |
| Reset view | Click the reset button |
| Crosshair | Toggle coordinate grid overlay |
| Pixel info | Hover any pixel to see flux and coordinates |

### Reading pixel values

Hover any pixel on the sky canvas to see:
- Pixel coordinates (x, y)
- World coordinates (RA, Dec)
- Flux value
- Detector band

### Stretch control

The stretch slider adjusts the min/max display range:

| Setting | Effect |
|---|---|
| Linear | Straight display of flux values |
| Log | Compresses dynamic range; good for faint features |
| Sqrt | Moderate compression |
| Power | Adjustable gamma correction |

Use **Log** or **Sqrt** to reveal faint details. Use **Linear** for accurate flux measurements.

---

## Blink Comparator

The blink comparator rapidly cycles through visits (epochs) to reveal variability and motion. This is the classic technique for finding variable stars and moving objects.

### Playback controls

| Control | Action |
|---|---|
| Play | Auto-cycle through all visits |
| Pause | Stop at current visit |
| Frame slider | Manually jump to any visit |
| Speed | Adjust blink rate (slow to fast) |

### What to look for

- **Variable stars** — Brightness changes between visits
- **Moving objects** — Position shifts between visits (asteroids, comets)
- **Artifacts** — Instrumental effects that appear in some visits only
- **New objects** — Objects that appear or disappear

### Tips

- Blink slowly to catch subtle motion
- Use the **Difference** view for enhanced moving-object detection
- Switch to **Gray** view for maximum sensitivity
- Use **Sub** (background-subtracted) to remove fixed sky background

---

## Detector Bands

SPHEREx has 6 detectors, each covering a different wavelength regime:

| Detector | Wavelength Range | Typical Use |
|---|---|---|
| D1 | 0.75-1.0 μm | Blue channel |
| D2 | 1.0-1.3 μm | Blue-red |
| D3 | 1.3-1.6 μm | Red |
| D4 | 1.6-2.0 μm | Red-IR |
| D5 | 2.0-2.5 μm | IR |
| D6 | 2.5-3.0 μm | Far-IR |

### Selecting bands

Click any combination of D1-D6 buttons. The sky canvas shows the selected detectors merged together.

### Merge modes

| Mode | Formula | Best For |
|---|---|---|
| Average | mean(D1, D2, ...) | Improving signal-to-noise |
| Sum | D1 + D2 + ... | Total flux measurements |
| Max | max(D1, D2, ...) | Isolating specific wavelength regimes |

**Use Average** when you want the cleanest possible image. **Use Max** when you want to isolate a specific detector's contribution. **Use Sum** when you want total flux.

---

## Spectrum Explorer

The Spectrum view extracts the per-pixel spectrum at the target position using SPHEREx's WCS-WAVE calibration.

### How it works

1. Hover any pixel on the sky canvas
2. The app extracts the spectrum at that position from cached cutouts
3. The spectrum is plotted as flux vs. wavelength, colored by detector
4. Each point represents one detector's effective wavelength/bandpass for that visit

### Wavelength bins

Click any region of the spectrum to bin the data and show statistics. The binned region shows:
- Mean flux
- Standard deviation
- Number of contributing detectors
- Effective wavelength

### Understanding the spectrum

- **X-axis**: Wavelength in microns (μm)
- **Y-axis**: Flux (arbitrary units)
- **Colors**: Each detector band has a distinct color
- **Points**: Each visit contributes one point per detector

### Limitations

The spectrum is extracted from cached cutouts at the target position. It is not a full spectral cube. Each point is one detector's effective wavelength/bandpass for that visit. For a full spectral cube, use the SPHEREx archive directly.

---

## Light Curves

The Lightcurve view plots brightness over time for the target.

### What it shows

- Flux measured at the target position across all visits
- Error bars from the variance extension
- Different detector bands plotted as separate series
- Vertical lines marking visit epochs

### Interactivity

- Hover any point to see the exact MJD (Modified Julian Date) and flux
- Click a point to jump to that visit in the Sky view
- Toggle individual detector bands on/off in the legend
- Zoom in on a time range by dragging a box

### Reading light curves

- **Flat line**: No variability; source is stable
- **Trending up/down**: Secular variation; possible accretion or pulsation
- **Spikes**: Outbursts; possible variability or artifact
- **Periodic**: Pulsating variable; measure the period

---

## Source Extraction

The Sources view detects point sources in the field from SPHEREx frame data.

### What it shows

- Detected sources plotted as markers on the sky canvas
- Source properties in a sortable table
- Color-magnitude diagram for selected sources

### Source properties

| Property | Source | Description |
|---|---|---|
| RA, Dec | Derived | Position |
| Flux | Frame-derived | Brightness |
| Band | SPHEREx detector | Spectral band |



### Filtering

Filter sources by:
- Magnitude range
- Color range
- Proper motion threshold
- Parallax significance

### Clicking sources

Click any source marker to:
- Highlight it on the sky canvas
- Show detailed properties
- Jump to its coordinates in the Sky view

---

## Catalog Overlays

The Overlays view overlays external catalogs on the sky canvas.

### Available catalogs

| Catalog | Provider | Sources | Best For |
|---|---|---|---|

### Toggle overlays

Show/hide each catalog independently. Overlays are color-coded:

### Hover details

Hover any marker to see:
- Source ID
- Coordinates
- Magnitude

---

## Ghost Trail Difference

The Difference view accumulates frame-to-frame differences so a moving object leaves a visible trail across visits. This is the classic blink-comparator technique for finding asteroids.

### How it works

1. Load a target with multiple visits
2. Open the **Difference** view
3. The app computes: frame(N) - frame(N-1) for each consecutive pair
4. Moving objects appear as paired positive/negative trails
5. Stationary objects cancel out

### What to look for

- **Paired trails**: Moving objects (one positive, one negative)
- **Single trails**: Possible new objects or artifacts
- **No trails**: All objects are stationary

### Tips

- Use the **Difference** view with the **Ghost trail** toggle enabled
- Adjust the **Stretch** to reveal faint trails
- Compare with the **Sky** view to confirm candidates

---

## Moving Object Detection

The Analysis view automatically detects moving objects across consecutive frames and predicts their trajectories.

### How it works

1. Load a target with multiple visits
2. Open the **Analysis** view
3. Click **Detect moving objects**
4. The app:
   - Detects candidates across consecutive frames
   - Groups detections into clusters
   - Fits a linear trajectory per cluster
   - Predicts future positions
   - Auto-classifies each candidate

### Candidate classes

| Class | Description | Typical Motion |
|---|---|---|
| Asteroid | Solar System object | Slow, predictable |
| Brown dwarf | Substellar object | Very slow or stationary |
| Variable star | Intrinsic brightness change | Stationary, flux varies |
| Artifact | Instrumental effect | Erratic, correlated with detector |

### Understanding results

Each candidate shows:
- Current position and predicted future positions
- Trajectory fit quality (R²)
- Motion speed (pixels/visit)
- Flux trend (increasing, decreasing, stable)
- Catalog match (if any)

### Tips

- Run detection on targets with many visits for best results
- Use the **Difference** view to manually verify candidates
- Check the **Sources** view for catalog matches

---

## Annotations

The Annotations layer is a citizen-science tool for marking features of interest.

### How to annotate

1. Open the **Annotations** view
2. Click **Add annotation**
3. Shift-drag on the sky canvas to draw a box around a feature
4. Add a description and category
5. Click **Save**

### Annotation categories

| Category | Example |
|---|---|
| Variable star | Brightness changes |
| Moving object | Position shifts |
| Artifact | Instrumental effect |
| Interesting feature | Anything noteworthy |
| Question | Mark for later investigation |

### Viewing annotations

Annotations appear as colored boxes on the sky canvas. Hover any box to see details. Click to edit or delete.

### Sharing

Annotations are stored per target and accessible via the public annotations API. Anyone viewing the same target can see your markings. This creates a community-driven citizen science layer.

---

## Collections

Save targets to collections for later review.

### Creating a collection

1. Open the **Collections** view
2. Click **New collection**
3. Enter a name and description
4. Click **Create**

### Adding targets to a collection

1. Open the target you want to save
2. Click **Add to collection**
3. Select the target collection
4. Click **Save**

### Viewing collections

Collections appear in the sidebar. Click any collection to load all its targets. You can:
- View all targets in a collection
- Remove targets from a collection
- Delete a collection
- Share a collection via URL

---

## Watchlist & Alerts

Save coordinates and get notified when new SPHEREx visits occur.

### Adding to watchlist

1. Open the **Watchlist** view
2. Click **Add to watchlist**
3. Enter coordinates and a label
4. Click **Save**

### Checking for alerts

The watchlist automatically polls for new visits since your last check. New-visit alerts appear in the watchlist view with:
- The coordinate that triggered the alert
- The number of new visits
- The date of the most recent visit

### Clicking an alert

Click any alert to load the new data. The Sky view updates with the latest visits.

### Managing watchlist entries

- Remove entries you no longer need
- Edit labels and coordinates
- Set check frequency (manual or automatic)

---

## Discovery Cards

Generate a tweet-ready PNG card sharing your discovery.

### Creating a card

1. Navigate to the view you want to share
2. Click **Export** → **Discovery card**
3. The app generates a PNG containing:
   - The current frame
   - Coordinates (RA, Dec)
   - Detector list
   - SPHEREx release
   - Visit time (MJD)
   - Project WATS branding

### Sharing

Save the PNG and share on social media. The card includes enough information for others to reproduce your view by entering the coordinates in the Sky view.

---

## Display Settings

### Display mode

Cycle through three display modes using the top-bar button:

| Mode | Description | Best For |
|---|---|---|
| Default | Full color, standard contrast | General viewing |
| IR grayscale | Pure black-and-white | Infrared work, blink comparison |
| High contrast | Maximum contrast | Faint features |

### Accessibility

- **Reduced motion**: Respects `prefers-reduced-motion` setting for blink playback
- **Alt text**: All canvases include descriptive alt text for screen readers
- **Keyboard navigation**: Full keyboard support for all controls
- **High contrast mode**: Maximum contrast for users with low vision

### Release selector

Choose between SPHEREx data releases:

| Release | Description |
|---|---|
| DR1 | Data Release 1 — first public release |
| QR3 | Quick Look Release 3 — most recent |
| QR2 | Quick Look Release 2 — earlier quick look |

Different releases may have different coverage and quality. The cache key includes the release, so switching releases produces independent cached results.

---

## Shareable URLs

All settings can be encoded in the URL for sharing. Anyone with the URL can load the exact same view.

### Basic URL

```
https://wats.pythonanywhere.com/?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3&name=WISE%200855
```

### URL parameters

| Parameter | Type | Description | Example |
|---|---|---|---|
| `ra` | float | Right Ascension (decimal degrees) | `133.795096` |
| `dec` | float | Declination (decimal degrees) | `-7.245147` |
| `fov` | float | Field of view (degrees) | `0.15` |
| `max_frames` | int | Number of visits | `6` |
| `name` | string | Target label (URL-encoded) | `WISE%200855` |
| `release` | string | DR1, QR3, or QR2 | `dr1` |
| `color` | bool | Enable color view | `1` |
| `subtract` | bool | Enable background subtraction | `1` |
| `blink` | bool | Enable blink mode | `1` |
| `sum` | bool | Enable sum merge | `1` |
| `background` | string | Background subtraction method | `median` |
| `stretch` | string | Image stretch type | `log` |

### Example URLs

**Preset share:**
```
https://wats.pythonanywhere.com/?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=6&name=WISE%200855&color=1
```

**Custom coordinate share:**
```
https://wats.pythonanywhere.com/?ra=262.93&dec=-26.89&fov=0.15&max_frames=6&release=dr1
```

**Blink comparison share:**
```
https://wats.pythonanywhere.com/?ra=262.93&dec=-26.89&blink=1&subtract=1
```

---

## API Reference

### Core routes

| Method | Route | Description | Returns |
|---|---|---|---|
| GET | `/api/health` | Service and cache status | JSON |
| GET | `/api/presets` | List of preset targets | JSON array |
| GET | `/api/releases` | Available SPHEREx releases | JSON |
| GET | `/api/frames` | Load or start fetching frames | JSON (200 or 202) |
| GET | `/api/status` | Fetch progress and manifest | JSON |
| GET | `/api/cancel` | Cancel a running fetch | JSON |
| GET | `/api/spectrum` | Spectrum at target position | JSON (200 or 202) |
| GET | `/api/overlays` | Catalog overlays | JSON |
| GET | `/frames/<key>/<filename>` | Rendered PNGs | PNG image |

### Target routes

| Method | Route | Description |
|---|---|---|
| GET | `/api/targets/<key>` | Target manifest and metadata |
| GET | `/api/targets/<key>/frames` | Frame list |
| GET | `/api/targets/<key>/spectrum` | Spectrum data |
| GET | `/api/targets/<key>/lightcurve` | Light curve data |
| GET | `/api/targets/<key>/sources` | Detected sources |
| GET | `/api/targets/<key>/quality` | Quality metrics |
| GET | `/api/targets/<key>/difference` | Frame differences |
| GET | `/api/targets/<key>/overlays` | Catalog overlays |
| GET | `/api/targets/<key>/export` | Export all data |

### Public routes

| Method | Route | Description |
|---|---|---|
| GET | `/api/public/targets/<key>/annotations` | Get annotations |
| POST | `/api/public/targets/<key>/annotations` | Add annotation |
| GET | `/api/public/collections` | List collections |
| POST | `/api/public/collections` | Create collection |
| POST | `/api/public/watchlist` | Add to watchlist |
| GET | `/api/public/watchlist/<id>/check` | Check for alerts |

### Analysis routes

| Method | Route | Description |
|---|---|---|
| POST | `/api/analysis/moving-objects` | Detect moving objects |
| POST | `/api/analysis/spectral-fit` | Fit spectra |

### Admin routes

| Method | Route | Description |
|---|---|---|
| GET | `/api/admin/cache` | Cache statistics |
| GET | `/api/admin/prefetch` | Prefetch presets |
| GET | `/api/admin/index` | Rebuild tile index |

### HTTP status codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 202 | Accepted (fetch started, poll for status) |
| 400 | Bad request (invalid parameters) |
| 404 | Not found |
| 500 | Internal server error |
| 502 | Bad gateway (IRSA unavailable) |
| 503 | Service unavailable (IRSA rate limited) |

---

## Troubleshooting

### "IRSA TAP is unavailable"

IRSA can be slow or return HTTP 503 under load. The app retries automatically with exponential backoff. If a fetch fails:

1. Wait a few minutes and click **Refresh data**
2. Check IRSA status at https://irsa.ipac.caltech.edu/
3. Try again later

### "Frame not found"

The frame may not have rendered yet. Check the status endpoint (`/api/status?key=<key>`) for progress. If the fetch completed but the frame is missing, re-run the fetch.

### "Target not found"

The target key may be invalid or the manifest may have been evicted. Re-load the coordinates to start a fresh fetch.

### Blank page

Check that:
- The WSGI module is `wsgi.py` (not `backend.app`)
- The working directory is the project root
- The web app has been reloaded after a deploy

### Slow loading

The unbundled frontend loads ES modules individually. This is slower than a bundled build but works without npm. To improve performance:

1. Run `npm install && npm run build` on a machine with Node.js
2. Commit the `dist/` directory
3. The app will serve the bundled frontend

### Custom coordinates fail on PythonAnywhere

PythonAnywhere blocks direct IRSA access. The app uses a relay (`WATS_TAP_RELAY` env var) to route IRSA traffic through a CORS proxy. If custom coordinates fail:

1. Check that `WATS_TAP_RELAY` is set in the web app environment variables
2. Verify the relay URL is correct
3. Try a different relay if the current one is down

### Presets don't load

Check that:
- The preset PNGs are committed to the repo
- The `data/presets/` directory exists
- The manifest files are present
- The web app has been reloaded

---

## Data Credits

Project WATS uses only public data and open-source software:

### Data sources

| Source | Provider | Usage |
|---|---|---|
| SPHEREx DR1 | NASA/IPAC IRSA | Primary image data |
| SPHEREx QR3 | NASA/IPAC IRSA | Quick look data |
| SPHEREx QR2 | NASA/IPAC IRSA | Earlier quick look |

### Software

| Software | Usage |
|---|---|
| Astropy | WCS reprojection, FITS handling |
| NumPy | Numerical computation |
| SciPy | Signal processing |
| Pillow | Image rendering |
| Flask | Web framework |

Full credits are in **CREDITS.md**.

---

## Contributing

See **CONTRIBUTING.md** for development setup, testing, and pull request guidelines.

---

## Team

The project is created by **Team WATS**. See **Team_WATS.md** for team information.

---

Thank you for using Project WATS. Explore the sky freely.