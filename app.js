import { getCurrentUser } from './supabase.js';

export const mockObservations = [
  {
    id: '1',
    species: 'Western Fence Lizard',
    approachability: 'Approach Safely',
    venomous: false,
    status: 'Common',
    habitat: 'in',
    confidence: 94,
    temp: 22,
    humidity: 61,
    distance: 45,
    lat: 32.88,
    lon: -117.23,
    timestamp: new Date(Date.now() - 300000).toISOString(),
    photo: null,
    notes: ''
  },
  {
    id: '2',
    species: 'Southern Pacific Rattlesnake',
    approachability: 'Do Not Approach',
    venomous: true,
    status: 'Common',
    habitat: 'out',
    confidence: 89,
    temp: 24,
    humidity: 58,
    distance: 120,
    lat: 32.87,
    lon: -117.24,
    timestamp: new Date(Date.now() - 600000).toISOString(),
    photo: null,
    notes: ''
  },
  {
    id: '3',
    species: 'Southern Alligator Lizard',
    approachability: 'Observe from Distance',
    venomous: false,
    status: 'Common',
    habitat: 'in',
    confidence: 91,
    temp: 21,
    humidity: 63,
    distance: 78,
    lat: 32.89,
    lon: -117.22,
    timestamp: new Date(Date.now() - 900000).toISOString(),
    photo: null,
    notes: ''
  },
  {
    id: '4',
    species: 'Orange-throated Whiptail',
    approachability: 'Observe from Distance',
    venomous: false,
    status: 'Vulnerable',
    habitat: 'in',
    confidence: 87,
    temp: 23,
    humidity: 60,
    distance: 55,
    lat: 32.86,
    lon: -117.25,
    timestamp: new Date(Date.now() - 1200000).toISOString(),
    photo: null,
    notes: ''
  }
];

export function startGeolocation(callback) {
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(
      (position) => {
        callback({
          lat: position.coords.latitude,
          lon: position.coords.longitude
        });
      },
      () => { callback({ lat: 32.88, lon: -117.23 }); }
    );
  } else {
    callback({ lat: 32.88, lon: -117.23 });
  }
}

export function startClock() {
  function updateClock() {
    const clockEl = document.getElementById('clock-time');
    if (clockEl) {
      clockEl.textContent = new Date().toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
    }
  }
  updateClock();
  setInterval(updateClock, 1000);
}

export function setActiveNav(tabName) {
  document.querySelectorAll('nav button').forEach(btn => btn.classList.remove('active'));
  document.querySelector(`nav button[data-tab="${tabName}"]`)?.classList.add('active');
}

export function checkAuthState() {
  const user = getCurrentUser();
  const isGuest = new URLSearchParams(window.location.search).get('guest') === 'true';
  if (!user && !isGuest) {
    window.location.href = 'login.html';
  }
  return { user, isGuest };
}

export function formatTimestamp(isoString) {
  const date = new Date(isoString);
  return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(isoString) {
  return new Date(isoString).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function getApproachabilityColor(approachability) {
  switch (approachability) {
    case 'Approach Safely': return '#4A7C59';
    case 'Observe from Distance': return '#E8923A';
    case 'Do Not Approach': return '#C0392B';
    default: return '#4A6B8A';
  }
}

export const speciesData = {
  'Western Fence Lizard': {
    status: 'Common / Safe',
    venomous: false,
    approachability: 'Approach Safely',
    habitat: 'San Diego foothills, chaparral, oak woodlands'
  },
  'Southern Pacific Rattlesnake': {
    status: 'Common',
    venomous: true,
    approachability: 'Do Not Approach',
    habitat: 'Coastal sage scrub, grasslands, canyon bottoms'
  },
  'Southern Alligator Lizard': {
    status: 'Common / Safe',
    venomous: false,
    approachability: 'Observe from Distance',
    habitat: 'Chaparral, oak woodlands, brushy areas'
  },
  'Western Skink': {
    status: 'Common / Safe',
    venomous: false,
    approachability: 'Approach Safely',
    habitat: 'Moist areas, near streams and springs'
  },
  'California King Snake': {
    status: 'Common / Safe',
    venomous: false,
    approachability: 'Observe from Distance',
    habitat: 'Mixed habitats, often near water sources'
  },
  'Orange-throated Whiptail': {
    status: 'Vulnerable',
    venomous: false,
    approachability: 'Observe from Distance',
    habitat: 'Sandy washes, coastal sage scrub'
  },
  'Western Side-blotched Lizard': {
    status: 'Common / Safe',
    venomous: false,
    approachability: 'Approach Safely',
    habitat: 'Open desert, sandy areas'
  },
  'San Diegan Legless Lizard': {
    status: 'Vulnerable',
    venomous: false,
    approachability: 'Observe from Distance',
    habitat: 'Grasslands, coastal sage scrub'
  }
};
