//! CPython's `random.Random`, bit for bit, for the parts the FundedNext
//! challenge Monte Carlo draws with: seeding from an int or a str (version 2,
//! SHA-512), `randrange(0, n)` and `sample(range(n), k)`. The research Monte
//! Carlo is reproduced path for path only if the dice are the same dice.

use sha2::{Digest, Sha512};

const N: usize = 624;
const M: usize = 397;

pub struct PyRandom {
    mt: [u32; N],
    index: usize,
}

impl PyRandom {
    fn init_genrand(seed: u32) -> [u32; N] {
        let mut mt = [0u32; N];
        mt[0] = seed;
        for i in 1..N {
            mt[i] = 1_812_433_253u32
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        mt
    }

    /// `init_by_array`, as `random_seed` calls it.
    fn from_key(key: &[u32]) -> Self {
        let mut mt = Self::init_genrand(19_650_218);
        let (mut i, mut j) = (1usize, 0usize);
        let length = key.len().max(1);
        for _ in 0..N.max(length) {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key.get(j).copied().unwrap_or(0))
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= length {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_566_083_941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
        }
        mt[0] = 0x8000_0000;
        Self { mt, index: N }
    }

    /// `random.Random(n)` for a non-negative int: its 32-bit words, least first.
    pub fn from_int(seed: u64) -> Self {
        let key: Vec<u32> = if seed == 0 {
            vec![0]
        } else if seed >> 32 == 0 {
            vec![seed as u32]
        } else {
            vec![seed as u32, (seed >> 32) as u32]
        };
        Self::from_key(&key)
    }

    /// `random.Random(s)` for a str: `int.from_bytes(s + sha512(s), "big")`.
    pub fn from_str(seed: &str) -> Self {
        let mut bytes = seed.as_bytes().to_vec();
        bytes.extend_from_slice(&Sha512::digest(seed.as_bytes()));
        // Big-endian integer -> little-endian 32-bit words, leading zero words
        // dropped as CPython's bit length drops them.
        let mut words: Vec<u32> = bytes
            .rchunks(4)
            .map(|chunk| chunk.iter().fold(0u32, |acc, b| (acc << 8) | u32::from(*b)))
            .collect();
        while words.len() > 1 && words.last() == Some(&0) {
            words.pop();
        }
        Self::from_key(&words)
    }

    fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            for k in 0..N {
                let y = (self.mt[k] & 0x8000_0000) | (self.mt[(k + 1) % N] & 0x7fff_ffff);
                let mut value = self.mt[(k + M) % N] ^ (y >> 1);
                if y & 1 != 0 {
                    value ^= 0x9908_b0df;
                }
                self.mt[k] = value;
            }
            self.index = 0;
        }
        let mut y = self.mt[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// `getrandbits(k)` for `k <= 32`.
    fn bits(&mut self, k: u32) -> u32 {
        self.next_u32() >> (32 - k)
    }

    /// `_randbelow_with_getrandbits(n)`.
    pub fn below(&mut self, n: usize) -> usize {
        let k = usize::BITS - n.leading_zeros();
        loop {
            let r = self.bits(k) as usize;
            if r < n {
                return r;
            }
        }
    }

    /// `randrange(0, n)`.
    pub fn randrange(&mut self, n: usize) -> usize {
        self.below(n)
    }

    /// `sample(range(n), k)` on the pool branch (always taken for `n <= 21`).
    pub fn sample_range(&mut self, n: usize, k: usize) -> Vec<usize> {
        let mut pool: Vec<usize> = (0..n).collect();
        let mut out = Vec::with_capacity(k);
        for i in 0..k {
            let j = self.below(n - i);
            out.push(pool[j]);
            pool[j] = pool[n - i - 1];
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Read off CPython 3 on 2026-10-21.
    #[test]
    fn matches_cpython() {
        let mut r = PyRandom::from_int(2026);
        assert_eq!((0..5).map(|_| r.randrange(480)).collect::<Vec<_>>(), [60, 163, 257, 262, 331]);
        let mut r = PyRandom::from_str("us500");
        assert_eq!(r.sample_range(20, 10), [12, 0, 3, 16, 9, 15, 10, 19, 18, 5]);
        let mut r = PyRandom::from_int(7003);
        assert_eq!((0..3).map(|_| r.randrange(190)).collect::<Vec<_>>(), [168, 31, 177]);
    }
}
