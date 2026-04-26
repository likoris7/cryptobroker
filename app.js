document.addEventListener('DOMContentLoaded', () => {
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.page-section');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            
            // Remove active class from all
            navItems.forEach(nav => nav.classList.remove('active'));
            sections.forEach(sec => sec.classList.add('d-none'));
            sections.forEach(sec => sec.classList.remove('active'));

            // Add active class to clicked
            item.classList.add('active');
            
            // Show corresponding section
            const targetId = item.getAttribute('data-target');
            const targetSection = document.getElementById(targetId);
            
            targetSection.classList.remove('d-none');
            // Small delay to allow display:block to apply before opacity animation
            setTimeout(() => {
                targetSection.classList.add('active');
            }, 10);
            
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    });
    // Modal Logic
    const btnSell = document.getElementById('btn-sell-business');
    const btnInvest = document.getElementById('btn-join-investors');
    const modalSell = document.getElementById('modal-sell');
    const modalInvest = document.getElementById('modal-invest');
    const closeBtns = document.querySelectorAll('.close-modal');

    if(btnSell) btnSell.addEventListener('click', () => modalSell.classList.add('show'));
    if(btnInvest) btnInvest.addEventListener('click', () => modalInvest.classList.add('show'));

    closeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const modalId = btn.getAttribute('data-modal');
            document.getElementById(modalId).classList.remove('show');
        });
    });

    window.addEventListener('click', (e) => {
        if (e.target.classList.contains('modal')) {
            e.target.classList.remove('show');
        }
    });

    // Secure input sanitizer (Anti-XSS string escape) - Relaxed for Telegram HTML parser
    function sanitizeHTML(str) {
        if(!str) return "";
        return str.replace(/[<>&"']/g, function(c) {
            switch(c) {
                case '<': return '&lt;';
                case '>': return '&gt;';
                case '&': return '&amp;';
                case '"': return '&quot;';
                case "'": return '&#39;';
                default: return c;
            }
        });
    }

    // Form Submission Logic MVP
    const forms = document.querySelectorAll('.lead-form');
    forms.forEach(form => {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            
            // SECURITY: Sanitize all inputs before any processing
            const inputs = form.querySelectorAll('input, select, textarea');
            let sanitizedData = {};
            inputs.forEach(input => {
                if(input.id) {
                    sanitizedData[input.id] = sanitizeHTML(input.value);
                }
            });
            
            // AUTOMATED FILTERING (Stop-words check)
            const forbiddenWords = ['casino', 'казино', 'рулетка', 'betting', 'ставки', 'gambling', 'гемблінг', 'darknet', 'scam', 'скам', 'porn', 'порно'];
            const textToScan = Object.values(sanitizedData).join(" ").toLowerCase();
            let isForbidden = false;
            
            for(let word of forbiddenWords) {
                if(textToScan.includes(word)) {
                    isForbidden = true;
                    break;
                }
            }
            
            const btn = form.querySelector('button[type="submit"]');
            if(isForbidden) {
                alert("Помилка: Ваш проект порушує правила платформи (заборонена тематика). Заявка відхилена автоматично.");
                btn.innerHTML = "Submitting..."; 
                btn.disabled = false;
                return; // Stop execution
            }

            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';
            btn.disabled = true;

            const sendToTelegram = () => {
                const BOT_TOKEN = "8574268421:AAFEO4FyDTJQsllojVtR7dvNB6P-9vtfI6w";
                const CHAT_ID = "969879267";
                
                let message = "";
                if (form.id === "form-sell") {
                    message = `🔥 <b>НОВА ЗАЯВКА НА ПРОДАЖ</b> 🔥\n\n` +
                              `👤 <b>Контакт:</b> ${sanitizedData['sell-name']}\n` +
                              `💼 <b>Тип:</b> ${sanitizedData['sell-type']}\n` +
                              `🔗 <b>Лінк:</b> ${sanitizedData['sell-url'] || 'Не вказано'}\n` +
                              `💰 <b>Ціна:</b> ${sanitizedData['sell-price']}\n` +
                              `📝 <b>Опис:</b>\n${sanitizedData['sell-desc']}`;
                } else {
                    message = `💎 <b>НОВИЙ ІНВЕСТОР (Whitelist)</b> 💎\n\n` +
                              `👤 <b>Контакт:</b> ${sanitizedData['invest-name']}\n` +
                              `📧 <b>Email:</b> ${sanitizedData['invest-email']}\n` +
                              `💰 <b>Бюджет:</b> ${sanitizedData['invest-budget']}`;
                }
                
                const tgUrl = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
                
                fetch(tgUrl, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        chat_id: CHAT_ID,
                        text: message,
                        parse_mode: "HTML"
                    })
                }).then(res => res.json()).then(res => {
                    if(!res.ok) {
                        alert("Telegram API Error: " + res.description);
                        btn.innerHTML = "Submit for Review";
                        btn.disabled = false;
                        return;
                    }
                    btn.style.display = 'none';
                    const successMsg = form.querySelector('.form-success');
                    successMsg.classList.remove('d-none');
                }).catch(err => {
                    console.error("Telegram API Error", err);
                    btn.innerHTML = "Submit for Review";
                    btn.disabled = false;
                    alert("Помилка відправки заявки. Спробуйте пізніше або зв'яжіться безпосередньо.");
                });
            };

            // GEO-BLOCKING: Check IP country before sending
            fetch('https://ipapi.co/json/')
                .then(response => response.json())
                .then(data => {
                    if (data.country_code === 'RU' || data.country_code === 'BY') {
                        alert("Доступ з вашого регіону обмежено.");
                        btn.innerHTML = "Submit for Review";
                        btn.disabled = false;
                        return; // Block execution
                    }
                    
                    // If not blocked, proceed with sending
                    sendToTelegram();
                })
                .catch(error => {
                    // Fail open: Adblocker might block ipapi.co. Proceed to Telegram!
                    console.log("Geo check failed (Adblock/Network), proceeding anyway");
                    sendToTelegram();
                });
        });
    });

    // Load custom businesses from Admin Panel
    loadCustomBusinesses();
    
    // Initialize Custom Selects
    setupCustomSelects();
});

function loadCustomBusinesses() {
    const grid = document.querySelector('.marketplace-grid');
    if (!grid) return;

    const data = localStorage.getItem('nextrade_businesses');
    if (!data) return; // No custom businesses added yet

    const businesses = JSON.parse(data);
    
    businesses.forEach(b => {
        const badgeColor = b.type === 'Full' ? 'bg-primary' : 'bg-secondary';
        const badgeText = b.category + (b.type === 'Fractional' ? ' (Fractional)' : '');
        
        let metricsHtml = '';
        if (b.roi) {
            metricsHtml += `
            <div class="metric">
                <i class="fa-solid fa-chart-line"></i>
                <span>ROI: ${b.roi}% / yr</span>
            </div>`;
        }
        if (b.revenue) {
            metricsHtml += `
            <div class="metric">
                <i class="fa-solid fa-money-bill-wave"></i>
                <span>${b.revenue}</span>
            </div>`;
        }

        const priceLabel = b.type === 'Fractional' ? 'Min. Investment' : 'Asking Price';
        const buttonText = b.type === 'Fractional' ? 'Invest Now' : 'View Deal';

        const card = document.createElement('div');
        card.className = 'listing-card glass custom-listing';
        // Add a fade-in animation for new elements
        card.style.animation = "fadeIn 0.5s ease-out forwards";
        
        card.innerHTML = `
            <div class="listing-img" style="background: url('${b.image_url}') center/cover no-repeat; border-bottom: 1px solid rgba(255,255,255,0.05);">
                <div class="listing-badge ${badgeColor}">${badgeText}</div>
            </div>
            <div class="listing-content">
                <h3>${b.title}</h3>
                <p class="listing-desc">${b.description}</p>
                
                <div class="metrics">
                    ${metricsHtml}
                </div>
                
                ${b.type === 'Fractional' ? `
                <div class="progress-container">
                    <div class="progress-bar" style="width: 0%"></div>
                </div>` : ''}
                
                <div class="listing-footer">
                    <div class="price">
                        <span class="price-label">${priceLabel}</span>
                        <span class="price-value">${b.price} ETH</span>
                    </div>
                    <button class="btn btn-primary">${buttonText}</button>
                </div>
            </div>
        `;

        // Prepend so new businesses show up first
        grid.insertBefore(card, grid.firstChild);
    });
}

// Custom Select UI Generator
function setupCustomSelects() {
    let x, i, j, l, ll, selElmnt, a, b, c;
    x = document.getElementsByTagName("select");
    l = x.length;
    for (i = 0; i < l; i++) {
        selElmnt = x[i];
        
        // Skip if already wrapped
        if(selElmnt.parentNode.classList.contains("custom-select-wrapper")) continue;

        // Create wrapper
        let wrapper = document.createElement("DIV");
        wrapper.setAttribute("class", "custom-select-wrapper");
        selElmnt.parentNode.insertBefore(wrapper, selElmnt);
        wrapper.appendChild(selElmnt);

        // Create selected DIV
        a = document.createElement("DIV");
        a.setAttribute("class", "select-selected form-control");
        let initialOpt = selElmnt.options[selElmnt.selectedIndex];
        a.innerHTML = initialOpt.innerHTML;
        if(initialOpt.hasAttribute('data-i18n')) a.setAttribute('data-i18n', initialOpt.getAttribute('data-i18n'));
        wrapper.appendChild(a);
        
        // Create dropdown DIV
        b = document.createElement("DIV");
        b.setAttribute("class", "select-items select-hide glass");
        
        ll = selElmnt.length;
        for (j = 0; j < ll; j++) {
            c = document.createElement("DIV");
            c.innerHTML = selElmnt.options[j].innerHTML;
            if(selElmnt.options[j].hasAttribute('data-i18n')) c.setAttribute('data-i18n', selElmnt.options[j].getAttribute('data-i18n'));
            
            c.addEventListener("click", function(e) {
                let s = this.parentNode.previousSibling.previousSibling; // The original select
                let h = this.parentNode.previousSibling; // The selected div
                for (let k = 0; k < s.length; k++) {
                    if (s.options[k].innerHTML == this.innerHTML || s.options[k].getAttribute('data-i18n') == this.getAttribute('data-i18n')) {
                        s.selectedIndex = k;
                        h.innerHTML = this.innerHTML;
                        if(this.hasAttribute('data-i18n')) h.setAttribute('data-i18n', this.getAttribute('data-i18n'));
                        let y = this.parentNode.getElementsByClassName("same-as-selected");
                        for (let p = 0; p < y.length; p++) {
                            y[p].removeAttribute("class");
                        }
                        this.setAttribute("class", "same-as-selected");
                        
                        // Trigger change event for original select
                        s.dispatchEvent(new Event('change'));
                        break;
                    }
                }
                h.click();
            });
            b.appendChild(c);
        }
        wrapper.appendChild(b);
        selElmnt.style.display = "none";
        
        a.addEventListener("click", function(e) {
            e.stopPropagation();
            closeAllSelect(this);
            this.nextSibling.classList.toggle("select-hide");
            this.classList.toggle("select-arrow-active");
        });
    }
}

function closeAllSelect(elmnt) {
    let x = document.getElementsByClassName("select-items");
    let y = document.getElementsByClassName("select-selected");
    let arrNo = [];
    for (let i = 0; i < y.length; i++) {
        if (elmnt == y[i]) {
            arrNo.push(i)
        } else {
            y[i].classList.remove("select-arrow-active");
        }
    }
    for (let i = 0; i < x.length; i++) {
        if (arrNo.indexOf(i) === -1) {
            x[i].classList.add("select-hide");
        }
    }
}

document.addEventListener("click", closeAllSelect);
