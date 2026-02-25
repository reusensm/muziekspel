export const handler = async () => {
  const client_id = process.env.SPOTIFY_CLIENT_ID;
  const client_secret = process.env.SPOTIFY_CLIENT_SECRET;

  const response = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Authorization': 'Basic ' + btoa(client_id + ':' + client_secret)
    },
    body: 'grant_type=client_credentials'
  });

  const data = await response.json();

  return {
    statusCode: 200,
    body: JSON.stringify({ access_token: data.access_token })
  };
};