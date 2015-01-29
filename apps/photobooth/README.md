PhotoBooth
==========

A JavaScript, Flash and Mojolicious webapp that emulates public
photobooths. You'll need a webcam and Flash support in your browser to
use it. Forked from https://github.com/diegok/PhotoBooth

Running locally
---------------

    ppm install Mojolicious
    ./photobooth.pl daemon
    
Point your browser to http://localhost:3000/

Check the http://mojolicio.us docs for other deployment options. 


Deploying to Stackato
---------------------

    stackato push -n


See also
--------

This program is just glue over other great software.

 * http://mojolicio.us/
 * http://code.google.com/p/jpegcam/
 * http://jquery.com/

Licence
-------

This program is free software; you can redistribute it and/or modify it
under the terms of either: the GNU General Public License as published
by the Free Software Foundation; or the Artistic License.

See http://dev.perl.org/licenses/ for more information.
