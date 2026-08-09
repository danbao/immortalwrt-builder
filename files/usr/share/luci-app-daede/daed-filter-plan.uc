#!/usr/bin/ucode

import { readfile } from 'fs';

function fail(message) {
	warn(message + '\n');
	exit(1);
}

function contains(values, needle) {
	for (let value in values)
		if (value == needle)
			return true;

	return false;
}

if (length(ARGV) != 4)
	fail('usage: daed-filter-plan.uc STATE_JSON SUBSCRIPTION_TAG GROUP_NAME EXCLUDE_KEYWORD');

let state = json(readfile(ARGV[0]));
let subscriptionTag = ARGV[1];
let groupName = ARGV[2];
let excludeKeyword = ARGV[3];
let subscriptions = state?.data?.subscriptions ?? [];
let groups = state?.data?.groups ?? [];
let subscription = null;
let group = null;

for (let candidate in subscriptions)
	if (candidate.tag == subscriptionTag) {
		subscription = candidate;
		break;
	}

for (let candidate in groups)
	if (candidate.name == groupName) {
		group = candidate;
		break;
	}

if (!subscription)
	fail('subscription not found: ' + subscriptionTag);

if (!group)
	fail('group not found: ' + groupName);

let desiredIds = [];
let existingIds = [];
let addIds = [];
let staleIds = [];
let subscriptionAttached = false;
let subscriptionFilterRegex = '';

for (let node in subscription.nodes.edges)
	if (index(node.name ?? '', excludeKeyword) < 0 &&
	    node.protocol != 'http' && node.protocol != 'https')
		push(desiredIds, node.id);

for (let node in group.nodes) {
	push(existingIds, node.id);

	if (node.subscriptionID == subscription.id && !contains(desiredIds, node.id))
		push(staleIds, node.id);
}

for (let id in desiredIds)
	if (!contains(existingIds, id))
		push(addIds, id);

for (let attached in group.subscriptions)
	if (attached.subscription?.id == subscription.id) {
		subscriptionAttached = true;
		subscriptionFilterRegex = attached.nameFilterRegex ?? '';
		break;
	}

print(sprintf('%J', {
	subId: subscription.id,
	groupId: group.id,
	subscriptionAttached,
	subscriptionFilterRegex,
	desiredCount: length(desiredIds),
	excludedCount: length(subscription.nodes.edges) - length(desiredIds),
	addCount: length(addIds),
	staleCount: length(staleIds),
	addIds,
	staleIds
}));
