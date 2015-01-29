#!/bin/bash
#echo "This script sets up a persistent .jenkins directory"

if [ -e "$STACKATO_FILESYSTEM"/.jenkins ]
  then
    echo "Jenkins directory exists, using existing data."
    ln -s "$STACKATO_FILESYSTEM"/.jenkins "$STACKATO_APP_ROOT"/.jenkins
else
    echo "Jenkins directory not found. Setting up..."

    # create folders in the shared filesystem 
    mkdir -p "$STACKATO_FILESYSTEM"/.jenkins
    ln -s "$STACKATO_FILESYSTEM"/.jenkins "$STACKATO_APP_ROOT"/.jenkins
fi

