---
title: Resource Acquisition Is Initialization
category: Idiom
language: en
tag:
 - Data access
---

## Intent
Resource Acquisition Is Initialization pattern can be used to implement exception safe resource management.

## Class diagram
![alt text](./etc/resource-acquisition-is-initialization.png "Resource Acquisition Is Initialization")

## Applicability
Use the Resource Acquisition Is Initialization pattern when

* You have resources that must be closed in every condition
