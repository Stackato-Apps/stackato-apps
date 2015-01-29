#!/usr/bin/env perl

use Mojolicious::Lite;
use LWP::Simple;
use Sys::HostIP;

any '/' => sub {
    my $self = shift;
    my $ip = Sys::HostIP->ip;

    $self->stash( ip => $ip, pid => $$ );
} => 'index';

app->start;

__DATA__

@@ index.html.ep

<!doctype html5>
<html>
<head>
<title>Stackato scaling test</title>
<style type="text/css">
body {
    background: black;
    font-family: Helvetica, sans-serif;
    color: #EEEEEE;
}

#prettybox {
    background: #212121;
    
    margin-top: 5em;
    padding-bottom: 1.5em;
    
    width: 30%;
    margin-left: auto;
    margin-right: auto;
    text-align: center;
    
    -moz-border-radius: 5px;
    border-radius: 5px;
}

#stackato {
    font-size: 2em;
    padding-top: 0.5em;
    padding-bottom: 1em;
}

.labeltext {
    padding-top: 1em;
    color: #999999;
}

.bigtext {
    padding-top: 0.3em;
    font-size: 3em;
}

#ip { 
    color: #3D5AD1;
}

#pid {
    color: #128236;
}

</style>
</head>
<body>
<div id="prettybox">
    <div id="stackato">stackato scaling test</div>

    <div id="ipblock">
	<div class="labeltext">the IP of this instance is:</div>
	<div class="bigtext" id="ip"><%= $ip %></div>
    </div>

    <div id="pidblock">
        <div class="labeltext">the PID of this process is:</div>
	<div class="bigtext" id="pid"><%= $pid %></div>
    </div>
</div>
</body>
</html>
