#!/usr/bin/ucode

'use strict';

import { readfile } from 'fs';

const REPOSITORY = 'danbao/immortalwrt-builder';
const TAG_PREFIX = 'openwrt-immortalwrt-x86-64-daed-';
const MAX_IMAGE_SIZE = 268435456;

function fail(message) {
	warn(message + '\n');
	exit(1);
}

function valid_digest(value) {
	return match(value ?? '', /^sha256:[0-9a-f]{64}$/) != null;
}

function asset(release, name) {
	let found = null;
	for (let candidate in release.assets ?? [])
		if (candidate.name == name) {
			if (found)
				fail('duplicate release asset: ' + name);
			found = candidate;
		}
	return found;
}

function valid_url(url, tag) {
	return index(url ?? '', 'https://github.com/' + REPOSITORY + '/releases/download/' + tag + '/') == 0;
}

if (ARGV[0] == 'status') {
	let installed = null;
	let plan = null;
	let verifiedIdentity = '';
	try { installed = json(readfile('/etc/immortalwrt-builder-release.json')); } catch (e) {}
	if (length(ARGV) > 1)
		try { plan = json(readfile(ARGV[1])); } catch (e) {}
	if (length(ARGV) > 2)
		try { verifiedIdentity = rtrim(readfile(ARGV[2])); } catch (e) {}
	print(sprintf('%J', {
		...(plan ?? {}),
		installedIdentity: installed?.identity_sha256,
		identityKnown: !!installed?.identity_sha256,
		verificationStatus: plan?.identity && verifiedIdentity == plan.identity ? 'verified' : 'not-verified'
	}));
	exit(0);
}

if (length(ARGV) < 2)
	fail('usage: immortalwrt-updater-plan.uc {status|select|validate} [ARGS]');

let releases = json(readfile(ARGV[1]));
if (type(releases) != 'array')
	fail('GitHub releases response is not an array');

let release = null;
for (let candidate in releases)
	if (!candidate.draft && !candidate.prerelease &&
	    index(candidate.tag_name ?? '', TAG_PREFIX) == 0 &&
	    match(candidate.tag_name, /^openwrt-immortalwrt-x86-64-daed-[0-9]{8}-[0-9a-f]+-[0-9a-f]{12}$/)) {
		release = candidate;
		break;
	}

if (!release)
	fail('no eligible daed release found');

let metadataAsset = asset(release, 'build-metadata.json');
if (!metadataAsset || !valid_digest(metadataAsset.digest) || !valid_url(metadataAsset.browser_download_url, release.tag_name))
	fail('release metadata asset is missing or untrusted');

if (ARGV[0] == 'select') {
	print(sprintf('%J', {
		tag: release.tag_name,
		publishedAt: release.published_at,
		metadataUrl: metadataAsset.browser_download_url,
		metadataDigest: metadataAsset.digest
	}));
	exit(0);
}

if (ARGV[0] != 'validate' || length(ARGV) < 3 || length(ARGV) > 5)
	fail('invalid updater plan mode');

let metadata = json(readfile(ARGV[2]));
let identity = metadata?.firmware_identity;
let releaseMetadata = metadata?.release;
if (metadata?.schema_version != 2 || metadata?.repository != REPOSITORY ||
    metadata?.flavor != 'daed' || metadata?.target != 'x86/64' ||
    identity?.repository != REPOSITORY || identity?.flavor != 'daed' ||
    identity?.target != 'x86/64' ||
    !match(identity?.identity_sha256 ?? '', /^[0-9a-f]{64}$/) ||
    releaseMetadata?.release_tag != release.tag_name)
	fail('release metadata does not describe a trusted daed x86/64 image');

let imageName = releaseMetadata.image_asset;
let imageSha256 = releaseMetadata.image_sha256;
if (!match(imageName ?? '', /^immortalwrt-x86-64-daed-[0-9]{8}-[0-9a-f]+-[0-9a-f]{12}\.img\.gz$/) ||
    !match(imageSha256 ?? '', /^[0-9a-f]{64}$/))
	fail('release image metadata is invalid');

let imageAsset = asset(release, imageName);
let checksumAsset = asset(release, imageName + '.sha256');
if (!imageAsset || !checksumAsset || !valid_digest(imageAsset.digest) ||
    !valid_digest(checksumAsset.digest) ||
    !valid_url(imageAsset.browser_download_url, release.tag_name) ||
    !valid_url(checksumAsset.browser_download_url, release.tag_name) ||
    imageAsset.size <= 0 || imageAsset.size > MAX_IMAGE_SIZE ||
    substr(imageAsset.digest, 7) != imageSha256)
	fail('release image assets are missing, oversized, or inconsistent');

let installed = null;
try { installed = json(readfile('/etc/immortalwrt-builder-release.json')); } catch (e) {}
let installedPublishedAt = ARGV[3] ?? '';
let installedIdentity = ARGV[4] ?? installed?.identity_sha256;
if (installedPublishedAt && release.published_at < installedPublishedAt)
	fail('candidate release is older than the installed release');

print(sprintf('%J', {
	tag: release.tag_name,
	publishedAt: release.published_at,
	immortalwrtVersion: metadata.immortalwrt?.version_code,
	identity: identity.identity_sha256,
	installedIdentity,
	updateAvailable: installedIdentity != identity.identity_sha256,
	imageName,
	imageSize: imageAsset.size,
	imageUrl: imageAsset.browser_download_url,
	imageDigest: imageAsset.digest,
	checksumUrl: checksumAsset.browser_download_url,
	checksumDigest: checksumAsset.digest
}));
