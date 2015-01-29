Developer Notes
===============

This document describes what the app does, and the relevant Stackato bits that
are involved.

== Termcast Servers

This termcast setup uses two different servers:

* https://github.com/jasonmay/app-termcast-server
* https://github.com/jasonmay/node-termcast-server

The first one listens for connections from a termcast client (a terminal that
wants to be termcasted), and the second one is a web server that displays the
terminals as HTML5 canvas objects. The 2 servers talk to each other over a
unix socket. The first server was written in Perl and the second one in
Node.js.

The Termcast client is available here:

* https://metacpan.org/pod/App::Termcast

== Termcast from Stackato

I decided to make this Stackato app for a few reasons. Setting up a termcast
server environment takes a while and is somewhat involved. Also the 2 servers
each have a ton of Perl and Node dependecies. Making it into an app, turns
that all into a push of a button.

Also, from a Stackato PoV, this setup had interesting challenges:

* Running 2 servers at once.
* Need 2 external ports.
* Need to install the deps quickly during staging.
* Bundling third party software:
  * Try not to make many Stackato specific changes.

The first thing I did was to fork the termcast server projects, so that I
could make some changes to them:

* https://github.com/ingydotnet/app-termcast-server
* https://github.com/ingydotnet/node-termcast-server

Then I created a 'stackato' branch on each one.

Next I wrote a pre-staging hook to clone them both from GitHub. I could have
included them in the app, but then I would have to push the code from my local
machine. Since my Stackato is in the Cloud, it was much faster to do a
cloud-to-cloud transfer during staging.

The dependencies took too long to install, but then I realized I could just
cache the installs since Stackato provides a stable runtime environment. I
installed a dummy Stackato instance, sshed in, and then installed all the Perl
and Node deps from there, to make sure they were built for Stackato. Then I
committed the install directories into the server repos. This effectively made
dependency installation go from 20 minutes to a few seconds (clone from
GitHub).

The Node server is the regular Stackato web piece and listens on $PORT. The
Perl server needs its own port so I used the Stackato Harbor feature which
worked perfectly.

I had to tweak the server code a tiny bit to make it pick up the correct
ports.

Finally I wrote a script `bin/web` to start the 2 servers.
