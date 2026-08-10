#!/usr/bin/env ucode

'use strict';

import { popen } from 'fs';

function run(command) {
	let process = popen(command + ' 2>&1', 'r');
	if (!process)
		return { code: -1, error: 'failed to start updater' };
	let output = process.read('all') ?? '';
	let code = process.close();
	if (code != 0)
		return { code, error: rtrim(output) };
	try {
		return { code: 0, data: json(output) };
	} catch (e) {
		return { code: 0, message: rtrim(output) };
	}
}

const methods = {};

methods.status = { call: function() { return run('/usr/sbin/immortalwrt-updater status'); } };
methods.check = { call: function() { return run('/usr/sbin/immortalwrt-updater check --refresh'); } };
methods.download = { call: function() { return run('/usr/sbin/immortalwrt-updater download'); } };
methods.verify = { call: function() { return run('/usr/sbin/immortalwrt-updater verify'); } };
methods.upgrade = {
	args: { snapshot_confirmed: false },
	call: function(request) {
		if (request.args.snapshot_confirmed !== true)
			return { code: 1, error: 'VMware snapshot confirmation is required' };
		return run('/usr/sbin/immortalwrt-updater upgrade --snapshot-confirmed');
	}
};

return { 'immortalwrt.updater': methods };
