#!/usr/bin/ucode

import { readfile } from 'fs';

function fail(message) {
	warn(message + '\n');
	exit(1);
}

if (length(ARGV) != 1)
	fail('usage: daed-import-links.uc IMPORT_RESPONSE_JSON');

let response = json(readfile(ARGV[0]));
let results = response?.data?.importSubscription?.nodeImportResult;
let valid = 0;

if (type(results) != 'array')
	fail('subscription import response has no node results');

for (let result in results) {
	let link = result?.link;

	if (result?.error != null || !result?.node || type(link) != 'string')
		continue;

	if (index(link, '\n') >= 0 || index(link, '\r') >= 0)
		fail('subscription node link contains a line break');

	print(link + '\n');
	valid++;
}

if (valid == 0)
	fail('subscription import produced no valid nodes');
