#!/bin/bash
docker run --publish=7474:7474 --publish=7687:7687 --env NEO4J_AUTH=none --volume=/home/onuralpyigit/neo4j_docker_vol:/data neo4j
