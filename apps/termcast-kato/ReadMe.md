## Name

termcast-kato - Termcasting for Stackato

## Synopsis

    # Run a termcast server on Stackato:
    git clone git@github.com:ingydotnet/termcast-kato
    cd termcast-kato
    stackato push -n

    # Open a web browser to the server app page.
    # Note the termcast server <address> and <port>.

    # Install the termcast client:
    cpanm App::Termcast

    # Start termcasting one of your terminal sessions:
    termcast --host <address> --port <port>

    # Your browser page should now show an entry for your termcast.
    # Click. Watch. Share. Enjoy.

## Description

Termcasting is sharing your terminal session on a web page for all to see.
This app will let you easily deploy a Termcast server on a Stackato PaaS, that
you and others can use.

The termcast session is readonly. People can watch your screen but they will
not be able to type anything.

## Installation

This doc assumes you have access to a Stackato account. Just push this app and
go to the URL for more info.

Your Stackato VM will need the Harbor feature turned on, since this app needs
2 ports (one for the web, and one for terminals to connent to it).

## Bugs

There is currently a bug where too much data from a terminal will clog the
server. To kill a termcast server, connect to it and run something like:

    find /

## Notes

See the DevNotes.md file for information about how this works, and interesting
notes about Stackato usage.

You can find the author as 'ingy' on #stackato on irc.freenode.net.

## Author

Written by Ingy döt Net <ingy@ingy.net>

## License and Copyright

The MIT License (MIT)

Copyright (c) 2014 Ingy döt Net
