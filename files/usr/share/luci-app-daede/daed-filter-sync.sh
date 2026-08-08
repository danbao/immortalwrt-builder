#!/bin/sh
# shellcheck shell=dash
# GraphQL variables intentionally use literal dollar signs in single quotes.
# shellcheck disable=SC2016

set -eu
umask 077

PLAN_HELPER="${DAED_PLAN_HELPER:-/usr/share/luci-app-daede/daed-filter-plan.uc}"
LOG="/tmp/luci-app-daede.filtered-sync.log"
LOCK="${DAED_SYNC_LOCK:-/tmp/luci-app-daede.filtered-sync.lock}"
TMPDIR=""

log() {
	printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"
}

fail() {
	log "ERROR: $*"
	printf '%s\n' "$*" >&2
	exit 1
}

cleanup() {
	[ -z "$TMPDIR" ] || rm -rf "$TMPDIR"
	rmdir "$LOCK" 2>/dev/null || true
}

json_escape() {
	printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

post_graphql() {
	local body="$1"
	local output="$2"
	local auth="${3:-}"

	if [ -n "$auth" ]; then
		# Feed the token through stdin so it is not exposed in curl's argv.
		printf 'header = "Authorization: Bearer %s"\n' "$auth" | \
			curl --config - --max-time 90 -fsS "$GRAPHQL_URL" \
			-H "Content-Type: application/json" \
			--data-binary "@$body" > "$output"
	else
		curl --max-time 30 -fsS "$GRAPHQL_URL" \
			-H "Content-Type: application/json" \
			--data-binary "@$body" > "$output"
	fi
}

graphql_checked() {
	local body="$1"
	local output="$2"

	post_graphql "$body" "$output" "$TOKEN" || fail "GraphQL request failed"
	if grep -q '"errors"' "$output"; then
		fail "GraphQL returned an error: $(cat "$output")"
	fi
}

query_state() {
	local body="$TMPDIR/state-body.json"
	local output="$1"

	printf '%s' '{"query":"query SyncState{subscriptions{id tag nodes{edges{id name protocol subscriptionID}}} groups{id name nodes{id name protocol subscriptionID} subscriptions{id tag}}}"}' > "$body"
	graphql_checked "$body" "$output"
}

make_plan() {
	local state="$1"
	local output="$2"

	ucode "$PLAN_HELPER" "$state" "$SUBSCRIPTION_TAG" "$GROUP_NAME" "$EXCLUDE_KEYWORD" > "$output" \
		|| fail "Failed to build filtered-node plan"
}

run_mutation() {
	local name="$1"
	local query="$2"
	local variables="$3"
	local body="$TMPDIR/${name}-body.json"
	local output="$TMPDIR/${name}-response.json"

	printf '{"query":"%s","variables":%s}' "$query" "$variables" > "$body"
	graphql_checked "$body" "$output"
}

[ "$#" -eq 3 ] || fail "usage: $0 SUBSCRIPTION_TAG GROUP_NAME EXCLUDE_KEYWORD"
SUBSCRIPTION_TAG="$1"
GROUP_NAME="$2"
EXCLUDE_KEYWORD="$3"

[ -x "$PLAN_HELPER" ] || fail "Missing plan helper: $PLAN_HELPER"
mkdir "$LOCK" 2>/dev/null || fail "Filtered subscription sync is already running"
trap cleanup EXIT INT TERM
TMPDIR="$(mktemp -d "/tmp/daed-filter-sync.XXXXXX")" || fail "Failed to create temporary directory"

LISTEN_ADDR="$(uci -q get daed.config.listen_addr)"
[ -n "$LISTEN_ADDR" ] || LISTEN_ADDR='127.0.0.1:2023'
case "$LISTEN_ADDR" in
	0.0.0.0:*) LISTEN_ADDR="127.0.0.1:${LISTEN_ADDR##*:}" ;;
	\[::\]:*) LISTEN_ADDR="127.0.0.1:${LISTEN_ADDR##*:}" ;;
esac
GRAPHQL_URL="http://${LISTEN_ADDR}/graphql"
USERNAME="$(uci -q get daed.config.dashboard_username)"
PASSWORD="$(uci -q get daed.config.dashboard_password)"
if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
	fail "Missing daed dashboard credentials"
fi
pidof daed >/dev/null 2>&1 || fail "daed is not running"

login_body="$TMPDIR/login-body.json"
login_response="$TMPDIR/login-response.json"
printf '{"query":"query Token($username:String!,$password:String!){token(username:$username,password:$password)}","variables":{"username":"%s","password":"%s"}}' \
	"$(json_escape "$USERNAME")" "$(json_escape "$PASSWORD")" > "$login_body"
post_graphql "$login_body" "$login_response" || fail "daed login request failed"
TOKEN="$(jsonfilter -i "$login_response" -e '@.data.token')"
[ -n "$TOKEN" ] || fail "daed login failed"

before_state="$TMPDIR/before-state.json"
before_plan="$TMPDIR/before-plan.json"
query_state "$before_state"
make_plan "$before_state" "$before_plan"
SUB_ID="$(jsonfilter -i "$before_plan" -e '@.subId')"
[ -n "$SUB_ID" ] || fail "Subscription ID is missing"

log "updating subscription tag=$SUBSCRIPTION_TAG"
run_mutation "update-subscription" \
	'mutation Update($id:ID!){updateSubscription(id:$id){id tag status}}' \
	"{\"id\":\"$(json_escape "$SUB_ID")\"}"

after_state="$TMPDIR/after-state.json"
after_plan="$TMPDIR/after-plan.json"
query_state "$after_state"
make_plan "$after_state" "$after_plan"

GROUP_ID="$(jsonfilter -i "$after_plan" -e '@.groupId')"
DESIRED_COUNT="$(jsonfilter -i "$after_plan" -e '@.desiredCount')"
EXCLUDED_COUNT="$(jsonfilter -i "$after_plan" -e '@.excludedCount')"
ATTACHED="$(jsonfilter -i "$after_plan" -e '@.subscriptionAttached')"
ADD_IDS="$(jsonfilter -i "$after_plan" -e '@.addIds')"
STALE_IDS="$(jsonfilter -i "$after_plan" -e '@.staleIds')"
ADD_COUNT="$(jsonfilter -i "$after_plan" -e '@.addCount')"
STALE_COUNT="$(jsonfilter -i "$after_plan" -e '@.staleCount')"

[ "${DESIRED_COUNT:-0}" -gt 0 ] || fail "Filter produced no usable nodes; keeping the existing group unchanged"

if [ "$ATTACHED" = "true" ]; then
	run_mutation "detach-subscription" \
		'mutation Detach($id:ID!,$ids:[ID!]!){groupDelSubscriptions(id:$id,subscriptionIDs:$ids)}' \
		"{\"id\":\"$(json_escape "$GROUP_ID")\",\"ids\":[\"$(json_escape "$SUB_ID")\"]}"
fi

if [ "${ADD_COUNT:-0}" -gt 0 ]; then
	run_mutation "add-nodes" \
		'mutation Add($id:ID!,$ids:[ID!]!){groupAddNodes(id:$id,nodeIDs:$ids)}' \
		"{\"id\":\"$(json_escape "$GROUP_ID")\",\"ids\":$ADD_IDS}"
fi

if [ "${STALE_COUNT:-0}" -gt 0 ]; then
	run_mutation "remove-stale-nodes" \
		'mutation Remove($id:ID!,$ids:[ID!]!){groupDelNodes(id:$id,nodeIDs:$ids)}' \
		"{\"id\":\"$(json_escape "$GROUP_ID")\",\"ids\":$STALE_IDS}"
fi

run_mutation "validate" 'mutation Validate($dry:Boolean!){run(dry:$dry)}' '{"dry":true}'
run_mutation "apply" 'mutation Apply($dry:Boolean!){run(dry:$dry)}' '{"dry":false}'

log "sync complete tag=$SUBSCRIPTION_TAG selected=$DESIRED_COUNT excluded=$EXCLUDED_COUNT"
printf 'Filtered subscription synced: selected=%s excluded=%s\n' "$DESIRED_COUNT" "$EXCLUDED_COUNT"
