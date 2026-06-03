// autoresize.js

// Функция для автоподстройки высоты
function adjustHeight(textarea) {
    textarea.style.height = 'auto'; // Сбрасываем высоту
    textarea.style.height = (textarea.scrollHeight) + 'px'; // Устанавливаем новую высоту
}

// Получаем все textarea на странице
document.querySelectorAll('textarea').forEach(function (textarea) {
    // Настроим высоту textarea при загрузке страницы
    adjustHeight(textarea);

    // Функция для автоподстройки высоты при вводе текста
    textarea.addEventListener('input', function () {
        adjustHeight(this);
    });
});
