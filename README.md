# tom_cfht
Canada-France-Hawaii Telescope facility module for TOM Toolkit.

## Pre-requisites

## Installation

Install the module into your TOM environment:

```shell
pip install tom-cfht
```

1. In your project `settings.py`, add `tom_cfht` to your `INSTALLED_APPS` setting:

    ```python
    INSTALLED_APPS = [
        ...
        'tom_cfht',
    ]
    ```

2. Add `tom_cfht.cfht.CFHTFacility` to the `TOM_FACILITY_CLASSES` in your TOM's
`settings.py`:
   ```python
    TOM_FACILITY_CLASSES = [
        'tom_observations.facilities.lco.LCOFacility',
        ...
        'tom_cfht.cfht.CFHTFacility',
    ]
   ```   

## Configuration

Include the following settings inside the `FACILITIES` dictionary inside `settings.py`:

```python
    FACILITIES = {
        ...
        'CFHT': {
            'CFHT_ACCESS_TOKEN': os.getenv('CFHT_ACCESS_TOKEN', 'please set CFHT_ACCESS_TOKEN'),
        },
    }
```

