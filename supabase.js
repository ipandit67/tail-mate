import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

const PROJECT_URL = 'https://kazkfrgbnsatagfckjpa.supabase.co';
const ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthemtmcmdibnNhdGFnZmNranBhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY1NDc4NzYsImV4cCI6MjA5MjEyMzg3Nn0.kOO1xWDlAwmFDoE7LNxa2xKkd7jHZacP4Fw4yeg6CcU';

export const supabase = createClient(PROJECT_URL, ANON_KEY);

async function sha256(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function signUp(username, password) {
  try {
    const password_hash = await sha256(password);
    const { error } = await supabase
      .from('users')
      .insert([{ username, password_hash }]);
    if (error) {
      if (error.code === '23505') return { success: false, error: 'Username already taken.' };
      return { success: false, error: error.message };
    }
    localStorage.setItem('tm_user', username);
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

export async function signIn(username, password) {
  try {
    const password_hash = await sha256(password);
    const { data, error } = await supabase
      .from('users')
      .select('username')
      .eq('username', username)
      .eq('password_hash', password_hash)
      .single();
    if (error || !data) return { success: false, error: 'Wrong username or password.' };
    localStorage.setItem('tm_user', username);
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

export function signOut() {
  localStorage.removeItem('tm_user');
  return { success: true };
}

export function getCurrentUser() {
  return localStorage.getItem('tm_user');
}

export async function saveObservation(data) {
  try {
    const { data: result, error } = await supabase
      .from('observations')
      .insert([data])
      .select();
    if (error) throw error;
    return { success: true, data: result[0] };
  } catch (error) {
    return { success: false, error: error.message };
  }
}

export async function updateObservation(id, data) {
  try {
    const { error } = await supabase
      .from('observations')
      .update(data)
      .eq('id', id);
    if (error) throw error;
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
}

export async function getObservationById(id) {
  try {
    const { data, error } = await supabase
      .from('observations')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return { success: true, data };
  } catch (error) {
    return { success: false, error: error.message };
  }
}

export async function getObservations() {
  try {
    const { data, error } = await supabase
      .from('observations')
      .select('*')
      .order('created_at', { ascending: false });
    if (error) throw error;
    return { success: true, data };
  } catch (error) {
    return { success: false, error: error.message };
  }
}

export function subscribeToObservations(callback) {
  const subscription = supabase
    .channel('observations')
    .on(
      'postgres_changes',
      { event: '*', schema: 'public', table: 'observations' },
      (payload) => { callback(payload); }
    )
    .subscribe();
  return () => supabase.removeChannel(subscription);
}
