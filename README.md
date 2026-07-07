# app_template
A template tomtoolkit app containing basic workflows, test packages, etc.

## How to use this Template

Once you have created your app, you should have a tree that looks like this:

```
.
├── .github
│   ├── dependabot.yml
│   └── workflows
│       ├── github-release.yml
│       ├── pypi-release.yml
│       ├── run-canary-tests.yml
│       └── run-tests.yml
├── .gitignore
├──LICENSE
├── poetry.lock
├── pyproject.toml
├── README.md
└── tom_app
    ├── admin.py
    ├── apps.py
    ├── __init__.py
    ├── migrations
    │   └── __init__.py
    ├── models.py
    ├── tests
    │   ├── boot_django.py
    │   ├── django_shell.py
    │   ├── factories.py
    │   ├── __init__.py
    │   ├── run_canary_tests.py
    │   ├── run_tests.py
    │   ├── tests_canary.py
    │   └── tests.py
    └── views.py
```

### Naming your App
During the creation on github, you will be asked to name your app. 
Generally for TOMToolkit Apps, we follow the naming convention ``tom_*``.
Once you have established a name and created the repo, you will need to alter the base name throughout the app.

Replace ``tom_app`` with your app's name in the following places

 * Base directory name (alongside this README)
 * tom_app.apps
   * TomAppConfig -> Tom_____Config (line 4)
   * name (line 6)
 * tom_app.tests.boot_django
   * APP_NAME (Line 10)
 * pyproject.toml
   * name (line2)
   * Also, fill out the description and authors while here. (line 3-6)
 * .github.workflows.github-release
   * line 28
   * line 39 (Be sure to keep dash rather than underscore)
 * .github.workflows.run-canary-tests
   * line 20
 * .github.workflows.run-tests
   * line 35
   * line 55
   * line 70 (also, your code to be included in the coverage check)

