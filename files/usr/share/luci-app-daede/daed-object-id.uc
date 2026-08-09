#!/usr/bin/ucode

import { readfile } from 'fs';

if (length(ARGV) != 3) {
	warn('usage: daed-object-id.uc STATE_JSON COLLECTION VALUE\n');
	exit(1);
}

let state = json(readfile(ARGV[0]));
let collection = ARGV[1];
let value = ARGV[2];
let data = state?.data ?? {};
let values = data[collection] ?? [];
let matches = [];

for (let item in values) {
	let candidate = collection == 'subscriptions' ? item.tag : item.name;
	if (candidate == value)
		push(matches, item.id);
}

if (length(matches) > 1) {
	warn('multiple matching objects found\n');
	exit(1);
}

if (length(matches) == 1)
	print(matches[0] + '\n');
