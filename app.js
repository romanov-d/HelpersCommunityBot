// Ждем, пока вся HTML-структура (DOM) загрузится
document.addEventListener('DOMContentLoaded', () => {

    // --- 1. Получаем объекты из Telegram ---
    
    // 'tg' — это главный объект для связи с Telegram
    const tg = window.Telegram.WebApp;
    // 'initData' — это секретная строка, которая
    // доказывает вашему боту, что это реальный юзер
    const initData = tg.initData;

    // --- 2. Находим наши HTML-элементы ---
    
    const tokenBalanceEl = document.getElementById('token-balance');
    const refLinkEl = document.getElementById('ref-link');
    const copyButtonEl = document.getElementById('copy-button');
    const joinDateEl = document.getElementById('join-date');

    // Сразу ставим "Загрузку..."
    tokenBalanceEl.innerText = "Загрузка...";
    joinDateEl.innerText = "Загрузка...";

    // --- 3. URL вашего бэкенда (бота) ---
    
    // !!! ВАЖНО: Мы заменим этот URL, когда настроим bot.py !!!
    // Это адрес, по которому мини-приложение будет "стучаться" к боту
    const BACKEND_URL = "https://www.helpers.ltd/get_user_data";

    // --- 4. Функция: Запрос данных у бота ---

    async function fetchUserData() {
        try {
            // Отправляем запрос на наш бэкенд (bot.py)
            const response = await fetch(BACKEND_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                // Отправляем наши "секретные" данные
                body: JSON.stringify({ initData: initData })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // Получаем ответ в формате JSON
            const data = await response.json();

            // { balance: 100, ref_link: "...", join_date: "..." }
            
            // Вставляем данные в HTML
            tokenBalanceEl.innerText = data.balance || 0;
            refLinkEl.value = data.ref_link || "Ошибка";
            
            // Форматируем дату (чтобы было красиво, "14.11.2025")
            if (data.join_date) {
                const joinDate = new Date(data.join_date);
                joinDateEl.innerText = joinDate.toLocaleDateString('ru-RU');
            } else {
                joinDateEl.innerText = "неизвестно";
            }

        } catch (error) {
            console.error('Ошибка при получении данных:', error);
            tokenBalanceEl.innerText = "Ошибка";
            joinDateEl.innerText = "Ошибка";
            tg.showAlert('Не удалось загрузить данные. Попробуйте позже.');
        }
    }

    // --- 5. Функция: Кнопка "Копировать" ---
    
    copyButtonEl.addEventListener('click', () => {
        const link = refLinkEl.value;
        if (!link || link === "Ошибка") return;

        // Используем API буфера обмена
        navigator.clipboard.writeText(link).then(() => {
            // Успешно скопировано
            copyButtonEl.innerText = "Скопировано!";
            
            // Даем "вибрацию" (если юзер на телефоне)
            tg.HapticFeedback.impactOccurred('light');

            // Возвращаем текст кнопки обратно через 1.5 секунды
            setTimeout(() => {
                copyButtonEl.innerText = "Копировать";
            }, 1500);

        }).catch(err => {
            console.error('Ошибка копирования:', err);
            tg.showAlert('Не удалось скопировать ссылку.');
        });
    });

    
    // --- 6. Запуск ---
    
    // Говорим Telegram, что приложение готово (убираем спиннер)
    tg.ready();
    // Запускаем загрузку данных
    fetchUserData();
});