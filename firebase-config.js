// firebase-config.js
// Налаштування бази даних Firebase
// Вставте сюди ключі вашого Firebase проєкту, щоб маркетплейс був доступний онлайн для всіх.
// Як отримати ключі читайте у README.md (Крок 5)

const firebaseConfig = {
    // РОЗКОМЕНТУЙТЕ І ЗАМІНІТЬ НА СВОЇ ДАНІ:
    // apiKey: "Ваш_API_Key",
    // authDomain: "ваша-назва.firebaseapp.com",
    // projectId: "ваша-назва",
    // storageBucket: "ваша-назва.appspot.com",
    // messagingSenderId: "123456789",
    // appId: "1:123456789:web:abcdef"
};

let db = null;
const isFirebaseConfigured = Object.keys(firebaseConfig).length > 0;

if (typeof firebase !== 'undefined') {
    if (isFirebaseConfigured) {
        firebase.initializeApp(firebaseConfig);
        db = firebase.firestore();
        console.log("Firebase успішно ініціалізовано!");
    } else {
        console.warn("Firebase не налаштовано. Використовується локальне сховище (localStorage) як Fallback. Лістинги не будуть публічними.");
    }
}
