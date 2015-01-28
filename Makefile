default: help

help:
	echo "Makefile targets: doc"

doc: ReadMe.pod

ReadMe.pod: doc/stackato-apps.swim
	swim --to=pod --complete --wrap $< > $@
