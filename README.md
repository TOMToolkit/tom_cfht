# tom_cfht
Canada-France-Hawaii Telescope facility module for TOM Toolkit.

## Pre-requisites

## Installation

Install the module into your TOM environment:

```shell
pip install tom-cfht
```

Then, in your project `settings.py`, add `tom_cfht` to your `INSTALLED_APPS` setting:

```python
INSTALLED_APPS = [
    ...
    'tom_cfht',
]
```

That's it. `tom_cfht` implements the `observation_facilities()` AppConfig integration point,
so the CFHT facility is discovered automatically — it does not need to be added to
`TOM_FACILITY_CLASSES` in your `settings.py`.

## Configuration

### Kealahou API access token

Observing-program and target-sync features talk to CFHT's Kealahou API, which authenticates
with an **API access token** — this is not your Kealahou web-UI password. Generate one in the
Kealahou web UI under *Account → Manage Tokens* (it is only shown once, at creation).

Each TOM user should save their token on their CFHT user profile (User Profile page). A
TOM-wide fallback token can be configured in the `FACILITIES` dictionary in `settings.py`;
a user's profile token, when set, takes precedence:

```python
    FACILITIES = {
        ...
        'CFHT': {
            'CFHT_ACCESS_TOKEN': os.getenv('CFHT_ACCESS_TOKEN', 'please set CFHT_ACCESS_TOKEN'),
        },
    }
```

## Target syncing with Kealahou

The CFHT facility page (*Facilities → CFHT* in the navbar) shows one tab per Kealahou
observing program. Each program's Targets section shows which targets are only in the
program's Target Grouping (the section is titled with the Target Grouping's name), only in
Kealahou, or in both — in agreement, or with property discrepancies shown field by field. Check rows in either direction and press **Sync selected targets** to import the
checked Kealahou targets into the TOM and upload the checked TOM targets to Kealahou.

Before syncing, each program must be associated with a **Target Grouping** — you choose an
existing one or create one (a name like `CFHT-MEGACAM-25BE25` is suggested) the first time
you open the program's Targets section. Membership in that Target Grouping is what marks a
target for syncing with the program: adding a target marks it for upload, downloads from
Kealahou land in it, and removing a member withdraws the target from TOM-side syncing (it
then shows under "In Kealahou only" again, ready to re-download). Only sidereal targets are
supported for now.

The CFHT observation form also shows the target's Kealahou status per program, with a
one-click upload (which also adds the target to the program's Target Grouping) for targets
that are not in Kealahou yet.

