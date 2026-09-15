const statusText = document.getElementById('status');
const button = document.getElementById('subscribe');
let publicKey;
let registration;
const theaters = [
  'CGV|강남','CGV|강변','CGV|건대입구','CGV|고덕강일','CGV|구로','CGV|대학로','CGV|동대문','CGV|등촌','CGV|남양주화도','CGV|다산',
  '메가박스|전체 지점',
  '롯데|가산디지털','롯데|건대입구','롯데|노원','롯데|신도림','롯데|에비뉴엘','롯데|영등포','롯데|용산','롯데|월드타워','롯데|청량리','롯데|홍대입구',
  '씨네큐|신도림','씨네큐|남양주다산'
];
const allTheaters = document.getElementById('all-theaters');
const theaterList = document.getElementById('theater-list');
theaters.forEach(value => {
  const label = document.createElement('label');
  label.innerHTML = `<input type="checkbox" value="${value}" checked> ${value.replace('|', ' ')}`;
  theaterList.appendChild(label);
});
allTheaters.addEventListener('change', () => {
  theaterList.querySelectorAll('input').forEach(input => input.checked = allTheaters.checked);
});
theaterList.addEventListener('change', () => {
  allTheaters.checked = [...theaterList.querySelectorAll('input')].every(input => input.checked);
});

function decodeKey(value) {
  return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
}

(async () => {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      throw new Error('iOS 16.4 이상에서 홈 화면에 추가한 앱으로 열어 주세요.');
    }
    const response = await fetch('./push-config.json', {cache: 'no-store'});
    if (!response.ok) throw new Error('알림 서버 설정을 준비 중입니다.');
    const config = await response.json();
    if (!config.publicKey) throw new Error('알림 서버 설정을 준비 중입니다.');
    publicKey = decodeKey(config.publicKey);
    registration = await navigator.serviceWorker.register('./sw.js');
    await navigator.serviceWorker.ready;
    button.disabled = false;
    statusText.textContent = '버튼을 눌러 알림 권한을 허용해 주세요.';
  } catch (error) {
    statusText.textContent = error.message;
  }
})();

button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    // Permission must be requested directly from the user's tap on iOS.
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('알림 권한이 허용되지 않았습니다. 아이폰 설정에서 이 앱의 알림을 확인해 주세요.');
    const subscription = await registration.pushManager.getSubscription() ||
      await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: publicKey});
    const selectedTheaters = [...theaterList.querySelectorAll('input:checked')].map(input => input.value);
    const registrationData = subscription.toJSON();
    registrationData.theaters = allTheaters.checked ? [] : selectedTheaters;
    document.getElementById('code').value = JSON.stringify(registrationData);
    document.getElementById('registration').hidden = false;
    statusText.textContent = '알림 권한 허용 완료 · 서버에 기기 등록 필요';
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.getElementById('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(document.getElementById('code').value);
    statusText.textContent = '등록 코드를 복사했습니다. 서버 연결 후 테스트 알림을 확인해 주세요.';
  } catch {
    document.getElementById('code').select();
    statusText.textContent = '코드를 길게 눌러 복사해 주세요.';
  }
});
