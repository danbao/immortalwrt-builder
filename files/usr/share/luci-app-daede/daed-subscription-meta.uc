#!/usr/bin/ucode

import { readfile } from 'fs';

if (length(ARGV) != 2) {
	warn('usage: daed-subscription-meta.uc STATE_JSON SUBSCRIPTION_TAG\n');
	exit(1);
}

let state = json(readfile(ARGV[0]));
let tag = ARGV[1];
let matches = [];

for (let subscription in (state?.data?.subscriptions ?? []))
	if (subscription.tag == tag)
		push(matches, subscription);

if (length(matches) != 1) {
	warn('expected exactly one subscription with the requested tag\n');
	exit(1);
}

let subscription = matches[0];
if (!subscription.id || !subscription.link) {
	warn('subscription is missing its id or source link\n');
	exit(1);
}

print(sprintf('%J', {
	id: subscription.id,
	link: subscription.link
}));
