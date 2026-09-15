const statusText = document.getElementById('status');
const button = document.getElementById('subscribe');
let publicKey;
let registration;
const theaterGroups = {
  'CGV': ['강남','강변','건대입구','고덕강일','구로','대학로','동대문','등촌','남양주화도','다산'],
  '메가박스': ['전체 지점'],
  '롯데시네마': ['가산디지털','건대입구','노원','신도림','에비뉴엘','영등포','용산','월드타워','청량리','홍대입구'],
  '씨네Q': ['신도림','남양주다산']
};
const allTheaters = document.getElementById('all-theaters');
const theaterList = document.getElementById('theater-list');
Object.entries(theaterGroups).forEach(([brand, branches]) => {
  const group = document.createElement('div');
  group.className = 'theater-group';
  group.innerHTML = `<h3>${brand} <label class="brand-all"><input type="checkbox" checked> 전체</label></h3>`;
  const brandAll = group.querySelector('h3 input');
  branches.forEach(branch => {
    const label = document.createElement('label');
    label.innerHTML = `<input type="checkbox" value="${brand === '롯데시네마' ? '롯데' : brand}|${branch}" checked> ${branch}`;
    group.appendChild(label);
  });
  brandAll.addEventListener('change', () => group.querySelectorAll('label:not(.brand-all) input').forEach(input => input.checked = brandAll.checked));
  group.addEventListener('change', () => { brandAll.checked = [...group.querySelectorAll('label:not(.brand-all) input')].every(input => input.checked); });
  theaterList.appendChild(group);
});
allTheaters.addEventListener('change', () => {
  theaterList.querySelectorAll('input').forEach(input => input.checked = allTheaters.checked);
});
theaterList.addEventListener('change', () => {
  allTheaters.checked = [...theaterList.querySelectorAll('label:not(.brand-all) input')].every(input => input.checked);
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
    const selectedTheaters = [...theaterList.querySelectorAll('label:not(.brand-all) input:checked')].map(input => input.value);
    const registrationData = subscription.toJSON();
    registrationData.theaters = allTheaters.checked ? [] : selectedTheaters;
    // New devices default to the currently requested September 30 screening.
    registrationData.dates = ['20260930'];
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
