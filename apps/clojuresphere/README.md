# ClojureSphere

Browsable dependency graph of Clojure projects. See it live here: http://www.clojuresphere.com/

## Deploying to Stackato

```bash
$ lein deps
$ stackato push -n
```

## Caveats

* Dev/test dependencies are included (might provide a way to filter them out)
* There may be project data missing here and there due to shortcuts taken when parsing project.clj and pom.xml files.
* Updating the list of projects is only semi-automated
* Only projects from GitHub and Clojars are included. Other sources may be added at some point.
* GitHub API doesn't return all Clojure projects, and doesn't return forks. Sometimes we can find the GitHub fork repos via Clojars links but not always. Ideas about how to overcome this welcome.
* The database powering the site is not a database; it's merely a Clojure data file read into memory.

## TODO

- move to a real database - Neo4J, other?
- figure out a feasible way to include github forks
- acquire more info from clojars (users, date stamps, etc.)
- fully automate data fetching & preprocessing
  - incremental github updates based on push date
- proper project.clj and pom.xml parsing
  - including version ranges (issue #1)
  - http://maven.apache.org/pom.html
- look for project.clj in sub-dirs (e.g., ring)
- track owners
- autocomplete search box
- think about how to incorporate usage stats of proprietary software that depends on open source libs
- separate/distinguish dev dependencies
  - different color or icon?
  - toggle to filter out entirely?
- "activity" field, to indicate how active a github project is
  - N commits in last month?
  - consider number of issues?
- dependents filter: latest versions, stable versions, all
- show a project's transitive dependencies
  - as a tree/graph? arborjs?
- marker/filter for java projects?
- make use of github "size" attribute
- user-defined tags
- put handler fns between routes & layout fns?
- reinstate pervasive ajax
- caching
- add rel=prev/next
- styling
  - ajax loading indicator
  - use clearfix
  - bigger click target for page heading
- scrape? https://github.com/search?langOverride=&language=&q=language%3Aclojure&repo=&start_value=1&type=Repositories


## Development

To refresh the project graph, copy `resources/config.clj.default` to `resources/config.clj` and update it with AWS credentials. Then run:

```
scripts/refresh.sh
```

This takes a long time - about 2 hours. The script pulls down project data from Clojars and GitHub, processes the data, and saves the result to S3.

## License

Copyright (C) 2011-2012 Justin Kramer

Distributed under the Eclipse Public License, the same as Clojure.
