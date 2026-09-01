# 3. Account strategy

Date: 2026-06-29

## Status

🤔 Proposed

## Context

We expect to have three major components for the Integration Hub: a Managed File Transfer service, an API Platform service, and an Events Platform service. We expect each component to have more than one environment in line with the split offered by the Modernisation Platform which provisions our accounts, baselines, and offers us common components.

## Decision

We will use separate accounts for each component of the Integration Hub, and separate accounts for each environment of each component.

## Consequences

- We accept that this will increase the number of accounts we manage and that it will increase the complexity of how our components interact with each other

- We will separate each component into its own account stack

- We will preserve account-level quotas to prevent contention between components

- We will isolate components to reduce their failure domain and scope of potential impacts