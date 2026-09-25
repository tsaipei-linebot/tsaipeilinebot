/* 全系統提醒改用彈出視窗（2026-09-25，見 HANDOFF.md「整個系統的提醒改成彈出視窗」）。
 *
 * - 頁面上加了 class="js-flash" 的訊息（操作結果：已儲存、已送出、錯誤…）會自動變成：
 *   成功（.success）→ 畫面右下角提示，4 秒後自動消失；
 *   錯誤／警告（.error／.warning）→ 彈出視窗，要按「確定」才關。
 *   原本那一段文字會藏起來，但還留在頁面裡（沒有 JS 也看得到、測試也驗得到）。
 * - 頁面上「固定的狀態提示」（例如「今日已收單」「有 N 筆未結案」）沒有加 js-flash，照原樣顯示。
 * - window.alert() 也換成同一個彈出視窗（系統裡用到 alert 的地方都是「提醒完就結束」，不需要擋住程式）；
 *   window.confirm() 維持瀏覽器原本的確認視窗。
 * - 提供 window.showAlert(文字, [清單]) 、window.showToast(文字) 給頁面自己呼叫。
 */
(function () {
    var dialog, dialogBody, toast, toastTimer;

    function ensureDom() {
        if (dialog) { return; }
        dialog = document.createElement('dialog');
        dialog.className = 'flash-dialog';
        dialogBody = document.createElement('div');
        dialogBody.className = 'flash-dialog-text';
        var actions = document.createElement('div');
        actions.className = 'btn-list';
        actions.style.flexDirection = 'row';
        actions.style.justifyContent = 'flex-end';
        var ok = document.createElement('button');
        ok.type = 'button';
        ok.className = 'btn btn-primary';
        ok.textContent = '確定';
        ok.addEventListener('click', function () { dialog.close(); });
        actions.appendChild(ok);
        dialog.appendChild(dialogBody);
        dialog.appendChild(actions);
        document.body.appendChild(dialog);

        toast = document.createElement('div');
        toast.className = 'flash-toast';
        toast.hidden = true;
        document.body.appendChild(toast);
    }

    function openDialog() {
        if (typeof dialog.showModal === 'function') {
            if (dialog.open) { dialog.close(); }
            dialog.showModal();
        } else {
            dialog.setAttribute('open', '');
        }
    }

    window.showAlert = function (message, items) {
        ensureDom();
        dialogBody.innerHTML = '';
        var p = document.createElement('p');
        p.style.margin = '0 0 8px';
        p.textContent = message || '';
        dialogBody.appendChild(p);
        if (items && items.length) {
            var ul = document.createElement('ul');
            ul.className = 'flash-dialog-list';
            items.forEach(function (item) {
                var li = document.createElement('li');
                li.textContent = item;
                ul.appendChild(li);
            });
            dialogBody.appendChild(ul);
        }
        openDialog();
    };

    window.showToast = function (message) {
        ensureDom();
        toast.textContent = message || '';
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.hidden = true; }, 4000);
    };

    window.alert = function (message) { window.showAlert(String(message == null ? '' : message)); };

    function showElement(el) {
        ensureDom();
        if (el.classList.contains('success')) {
            window.showToast(el.textContent.trim());
        } else {
            // 保留原本的連結等內容（例如「已錄取○○，人員已經加到查詢人員」裡的連結）
            dialogBody.innerHTML = '';
            Array.prototype.forEach.call(el.childNodes, function (node) {
                dialogBody.appendChild(node.cloneNode(true));
            });
            openDialog();
        }
        el.hidden = true;
    }

    function run() {
        var items = document.querySelectorAll('.js-flash');
        var errors = [], successes = [];
        Array.prototype.forEach.call(items, function (el) {
            if (!el.textContent.trim()) { return; }
            (el.classList.contains('success') ? successes : errors).push(el);
        });
        successes.forEach(showElement);
        // 一次只開一個視窗：多個錯誤合併顯示
        if (errors.length === 1) {
            showElement(errors[0]);
        } else if (errors.length > 1) {
            ensureDom();
            dialogBody.innerHTML = '';
            errors.forEach(function (el) {
                var block = document.createElement('div');
                block.style.marginBottom = '8px';
                Array.prototype.forEach.call(el.childNodes, function (node) { block.appendChild(node.cloneNode(true)); });
                dialogBody.appendChild(block);
                el.hidden = true;
            });
            openDialog();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();
