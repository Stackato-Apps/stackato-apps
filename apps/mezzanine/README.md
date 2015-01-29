Mezzanine
---------

This a vanilla Mezzanine CMS project that is ready to deploy straight 
to a Stackato cloud. This is the development branch of this repo, meaning
that it may not be stable for production environments.

**Local development**

`pip install -r requirements.pip`  
`python manage.py syncdb --noinput`  
`python manage.py runserver`

**Deploying to stackato**

`stackato push -n`

**Default admin user**

The default admin user credentials are:

u: admin  
p: default

You can login with these credentials at /admin. It is recommended that you 
change this default password by running  
`stackato run python manage.py changepassword`
