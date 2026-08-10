'use strict';
'require view';
'require rpc';
'require ui';
'require dom';

var callStatus = rpc.declare({ object: 'immortalwrt.updater', method: 'status' });
var callCheck = rpc.declare({ object: 'immortalwrt.updater', method: 'check' });
var callDownload = rpc.declare({ object: 'immortalwrt.updater', method: 'download' });
var callVerify = rpc.declare({ object: 'immortalwrt.updater', method: 'verify' });
var callUpgrade = rpc.declare({ object: 'immortalwrt.updater', method: 'upgrade', params: ['snapshot_confirmed'] });

function renderResult(result) {
	if (!result || result.code !== 0)
		return E('div', { class: 'alert-message error' }, [result && result.error || _('操作失败')]);
	var data = result.data || {};
	return E('table', { class: 'table' }, [
		E('tr', {}, [E('td', {}, [_('Release')]), E('td', {}, [data.tag || '-'])]),
		E('tr', {}, [E('td', {}, [_('ImmortalWrt')]), E('td', {}, [data.immortalwrtVersion || '-'])]),
		E('tr', {}, [E('td', {}, [_('发布时间')]), E('td', {}, [data.publishedAt || '-'])]),
		E('tr', {}, [E('td', {}, [_('镜像大小')]), E('td', {}, [data.imageSize ? '%d MiB'.format(data.imageSize / 1048576) : '-'])]),
		E('tr', {}, [E('td', {}, [_('可更新')]), E('td', {}, [data.updateAvailable ? _('是') : _('否')])]),
		E('tr', {}, [E('td', {}, [_('当前固件身份')]), E('td', {}, [data.identityKnown ? data.installedIdentity : _('身份未知')])]),
		E('tr', {}, [E('td', {}, [_('候选固件身份')]), E('td', {}, [data.identity || '-'])]),
		E('tr', {}, [E('td', {}, [_('校验状态')]), E('td', {}, [data.verificationStatus || _('未校验')])])
	]);
}

return view.extend({
	load: function() { return callStatus(); },
	render: function(result) {
		var output = E('div', {}, [renderResult(result)]);
		var snapshot = E('input', { type: 'checkbox' });
		function invoke(call, args) {
			ui.showModal(_('处理中'), [E('p', { class: 'spinning' }, [_('请勿关闭页面。')])]);
			return (args === undefined ? call() : call(args)).then(function(res) {
				ui.hideModal();
				dom.content(output, renderResult(res));
			});
		}
		return E([], [
			E('h2', {}, [_('ImmortalWrt 固件更新')]),
			E('p', {}, [_('更新源固定为 danbao/immortalwrt-builder 的 daed x86/64 Release。系统只自动检查，不会自动刷写。')]),
			output,
			E('div', { class: 'cbi-page-actions' }, [
				E('button', { class: 'btn cbi-button-action', click: function() { return invoke(callCheck); } }, [_('检查更新')]),
				' ',
				E('button', { class: 'btn cbi-button-apply', click: function() { return invoke(callDownload); } }, [_('下载并校验')]),
				' ',
				E('button', { class: 'btn', click: function() { return invoke(callVerify); } }, [_('重新校验')])
			]),
			E('p', {}, [snapshot, ' ', _('我已为 OpenWrt 虚拟机创建 VMware 快照（snapshot_confirmed）')]),
			E('button', {
				class: 'btn cbi-button-negative',
				click: function() {
					if (!snapshot.checked) {
						ui.addNotification(null, E('p', {}, [_('必须先确认 VMware 快照。')]), 'error');
						return;
					}
					return invoke(callUpgrade, true);
				}
			}, [_('安装更新并重启')])
		]);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
